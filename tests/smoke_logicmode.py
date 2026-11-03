# With AI detection off, the nodes that can stand alone keep running.
#
# Tok Aba: "only the detection stream and other nodes that connect to it
# will be affecting if the ai detection is not running". So it is decided by
# the GRAPH: a node waits for detection when a Detection Stream reaches it,
# through any wire, and every other node runs -- whatever its type. A node
# with mixed inputs (a zone count AND an Inject) waits: it must not compute
# with a zero standing in.
#
# Since 2026-09-30 there is ONE app per system and AI detection is a switch
# inside it (app/run.py ai_config); this file pins the decision and the app
# supervisor that keeps the app up. The switch itself, toggled in a running
# app with fake hardware, is tests/smoke_aiswitch.py.
#
# What is pinned here:
#   1. reachability: direct, transitive, through a Link out/in, through a
#      function Call; mixed inputs wait; outputs and cards not fed run
#   2. the canvas is told which DRAWN nodes wait (links and Calls included)
#   3. the list reaches config.local.toml [logic] and, with the AI off, the
#      app runs exactly it (a Python node not fed runs; a fed one, or a zone
#      rule, sits out) without rebuilding the engine
#   4. reconcile(): the app is up whenever the system is online, AI off
#      when it has to be started; an old logic-mode app is replaced once
#   5. the triggers are wired: master start, save, both Start/Stop
#      switches; Power off, delete and a counters reset stop the whole app
#   6. a crashed app is restarted with the AI off, with a back-off; a crash
#      while detecting says so; a clean stop is not a crash
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc               # noqa: E402
from core.services import app_supervisor as lr                  # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def node(nid, typ, **data):
    base = dict((wf.REGISTRY.get(typ) or {}).get("data") or {})
    return {"id": nid, "type": typ, "position": {"x": 0, "y": 0},
            "data": {**base, **data}}


def edge(src, tgt, sh=None, th=None):
    return {"id": f"e_{src}_{sh or ''}_{tgt}_{th or ''}", "source": src,
            "target": tgt, "sourceHandle": sh, "targetHandle": th}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], ["person"], ["person"])


def fid(g, nid):
    return next(n for n in g["nodes"] if n["id"] == nid)["data"]["fnId"]


# -- 1. reachability ------------------------------------------------------------
nodes = [
    node("cam", "source.detection"),
    node("cnt", "analytics.frame", name="People", mode="realtime"),
    # Transitive: a Math of the count alone.
    node("dbl", "math.arith", name="Double", op="mul", b_value=2),
    # Mixed: the count AND an Inject.
    node("mix", "math.arith", name="Mixed", op="add"),
    node("inj", "common.inject", name="Kick", ptype="number", payload="1"),
    # Not fed at all: a Math of the Inject, a card and an MQTT alert on it.
    node("half", "math.arith", name="Half", op="div", b_value=2),
    node("card", "dashboard.card", name="Kicked"),
    node("mq", "output.mqtt"),
    # An output fed by detection.
    node("mq2", "output.mqtt"),
]
edges = [edge("cam", "cnt"), edge("cnt", "dbl", th="a"),
         edge("cnt", "mix", th="a"), edge("inj", "mix", th="b"),
         edge("inj", "half", th="a"), edge("inj", "card"), edge("inj", "mq"),
         edge("dbl", "mq2")]
g, out = compile_(nodes, edges)
check("the test graph compiles", not out["errors"], out["errors"])
free = out["frames_free"]
free_fns = set(free["functions"])
check("an Inject runs without detection", fid(g, "inj") in free_fns, free)
check("...and a Math fed only by it", fid(g, "half") in free_fns, free)
check("a rule fed by the Detection Stream waits", fid(g, "cnt") not in free_fns)
check("...and what it feeds, transitively", fid(g, "dbl") not in free_fns)
check("a MIXED input (a count AND an Inject) waits", fid(g, "mix") not in free_fns)
check("an output fed only by the Inject runs", "wf_mq" in free["outputs"], free)
check("an output fed by detection waits",
      "wf_mq2" not in free["outputs"]
      and any(o["id"] == "wf_mq2" for o in out["outputs"]), free)
waits = set(out["waits_for_detection"])
check("the canvas is told the fed nodes wait",
      {"cam", "cnt", "dbl", "mix", "mq2"} <= waits, sorted(waits))
check("...and not the rest (the card on the Inject included)",
      not ({"inj", "half", "card", "mq"} & waits), sorted(waits))

# Through a Link out / Link in: the jump is a wire.
nodes = [node("cam", "source.detection"),
         node("cnt", "analytics.frame", name="People", mode="realtime"),
         node("lo", "common.link_out", link="far"),
         node("li", "common.link_in", link="far"),
         node("far", "math.arith", name="Far", op="mul", b_value=3),
         node("inj", "common.inject", name="Kick"),
         node("lo2", "common.link_out", link="near"),
         node("li2", "common.link_in", link="near"),
         node("near", "math.arith", name="Near", op="mul", b_value=3)]
edges = [edge("cam", "cnt"), edge("cnt", "lo"), edge("li", "far", th="a"),
         edge("inj", "lo2"), edge("li2", "near", th="a")]
g, out = compile_(nodes, edges)
free_fns = set(out["frames_free"]["functions"])
check("through a Link out/in, the far side waits",
      fid(g, "far") not in free_fns and fid(g, "near") in free_fns,
      out["frames_free"])
waits = set(out["waits_for_detection"])
check("...and the canvas marks both Link nodes on the fed jump",
      {"lo", "li", "far"} <= waits and not ({"lo2", "li2", "near"} & waits),
      sorted(waits))

# Through a function Call: its copy of the body waits when its argument does.
nodes = [node("cam", "source.detection"),
         node("cnt", "analytics.frame", name="People", mode="realtime"),
         node("inj", "common.inject", name="Kick"),
         node("s1", "function.start", fn="rate", params=["n"]),
         node("r1", "math.round", op="round", name="Rate"),
         node("x1", "function.end", fn="rate"),
         node("k0", "function.call", fn="rate"),
         node("k1", "function.call", fn="rate")]
edges = [edge("cam", "cnt"), edge("s1", "r1", sh="p_n", th="a"),
         edge("r1", "x1"), edge("cnt", "k0", th="p_n"),
         edge("inj", "k1", th="p_n")]
g, out = compile_(nodes, edges)
check("the function graph compiles", not out["errors"], out["errors"])
free_fns = set(out["frames_free"]["functions"])
copies = sorted(f["id"] for f in out["functions"] if f["id"].endswith("__" + fid(g, "r1")))
check("the copy called from the count waits; the one called from the Inject "
      "runs", [c for c in copies if c in free_fns] == [f"k1__{fid(g, 'r1')}"],
      (copies, free_fns))
waits = set(out["waits_for_detection"])
check("...and the canvas marks the fed Call and the body it copies",
      {"k0", "r1"} <= waits and "k1" not in waits, sorted(waits))

# A formula naming a camera key BY HAND waits too: there is no wire to follow,
# so the key itself is what says so -- and one reading such a rule's value.
docs = [{"id": "typed", "type": "expr", "params": {"expr": "zone_bay_count + 1"}},
        {"id": "then", "type": "expr", "params": {"expr": "fn_typed_value * 2"}},
        {"id": "own", "type": "expr", "params": {"expr": "fn_kick_value * 2"}},
        {"id": "kick", "type": "inject", "params": {}}]
free = wf.logic_artifacts(docs, [], set(), {}, {}, {}, [])
check("a formula reading a zone_ key by name waits, and what reads it",
      free["functions"] == ["own", "kick"], free)

# -- 3. into the config, and the app keeps exactly it ----------------------------
pkg = tempfile.mkdtemp()
real_pkg = dc._pkg_dir
dc._pkg_dir = lambda sd, slug: pkg
try:
    dc.set_logic("/x", "demo", {"functions": ["kick", "py"], "outputs": ["wf_mq"]})
    written = dc._read_toml(dc._local_path(pkg)).get("logic")
finally:
    dc._pkg_dir = real_pkg
check("the list lands in config.local.toml [logic]",
      written == {"functions": ["kick", "py"], "outputs": ["wf_mq"],
                  "version": 0}, written)
FNS = [{"id": "kick", "type": "inject"}, {"id": "py", "type": "custom"},
       {"id": "pyfed", "type": "custom"}, {"id": "zc", "type": "zone_count"}]
cfg = apprun.ai_config({
    "ai": {"on": False}, "logic": written, "functions": FNS,
    "outputs": [{"id": "wf_mq", "type": "mqtt"},
                {"id": "wf_mq2", "type": "mqtt"}]})
check("AI off: the app keeps what the list names -- a Python node not fed "
      "included -- and every other rule sits out",
      cfg["_ai_inactive"] == ["pyfed", "zc"], cfg["_ai_inactive"])
check("...the functions list itself is untouched (the engine is not rebuilt)",
      cfg["functions"] == FNS, cfg["functions"])
check("...and only the outputs it names fire",
      [o["id"] for o in cfg["outputs"]] == ["wf_mq"], cfg["outputs"])
cfg = apprun.ai_config({"ai": {"on": False},
                        "logic": {"functions": ["zc"], "outputs": []},
                        "functions": [{"id": "zc", "type": "zone_count"}]})
check("a type that reads detections never runs with the AI off, whatever "
      "the list says", cfg["_ai_inactive"] == ["zc"], cfg["_ai_inactive"])
on_cfg = {"logic": written, "functions": FNS, "cameras": [{"id": "c1"}],
          "detector": {"kind": "hailo"}}
check("AI on (or no [ai] table at all): the config is the config",
      apprun.ai_config(on_cfg) is on_cfg
      and apprun.ai_config(dict(on_cfg, ai={"on": True}))["detector"]
      == {"kind": "hailo"})
off = apprun.ai_config(dict(on_cfg, ai={"on": False},
                            cameras=[{"id": "c0", "enabled": False},
                                     {"id": "c1"}, {"id": "c2"}]))
check("AI off: no detector (the chip is never opened), every camera parked",
      off["detector"] == {"kind": "none"}
      and all(c["enabled"] is False for c in off["cameras"]), off)
check("...with the cameras detection runs FIRST, so the pipe holding the "
      "free rules is the one detection makes primary",
      [c["id"] for c in off["cameras"]] == ["c1", "c2", "c0"], off["cameras"])
legacy = apprun.ai_config({"ai": {"on": False},
                           "camera": {"source": "0", "fps": 15}})
check("...a legacy [camera]-only system parks that camera too",
      [c["id"] for c in legacy["cameras"]] == [apprun.MAIN_CAMERA]
      and legacy["cameras"][0]["enabled"] is False, legacy["cameras"])


# -- 4. reconcile(): the app is up whenever the system is online ---------------------
class Box:
    """A fake of the dc calls the supervisor makes, recording what it does.
    `mode` is the running app's: unified | logic (an old logic-mode app) |
    detection (an old detection app) | None (nothing running)."""

    def __init__(self, mode=None, ai=False, supported=True, cfg=None,
                 start_ok=True, off=False):
        self.mode, self.ai = mode, ai
        self.supported, self.start_ok, self.off = supported, start_ok, off
        self.cfg = cfg if cfg is not None else {
            "logic": {"functions": ["kick"], "outputs": [],
                      "version": wf.LOGIC_VERSION}}
        self.calls = []
        self.raw = {}

    def install(self):
        self.saved = (dc.status, dc.system_config, dc.unified_supported,
                      dc.ensure_app, dc.stop_app, dc.app_state, dc.powered_off)
        dc.powered_off = lambda sd, slug: self.off
        dc.status = lambda sd, slug: {
            "app_running": self.mode is not None,
            "running": self.mode == "detection" or bool(
                self.mode == "unified" and self.ai),
            "logic_running": self.mode == "logic" or bool(
                self.mode == "unified" and not self.ai)}
        dc.system_config = lambda sd, slug: self.cfg
        dc.unified_supported = lambda sd, slug: self.supported
        dc.ensure_app = self._ensure
        dc.stop_app = self._stop
        dc.app_state = lambda sd, slug: dict(self.raw)
        return self

    def _ensure(self, sd, slug):
        if self.mode not in (None, "logic"):
            return {"ok": True, "word": "running"}
        word = "replaced" if self.mode == "logic" else "started"
        self.calls.append("ensure:" + word)
        if self.start_ok:
            self.mode, self.ai = "unified", False
            return {"ok": True, "word": word}
        self.mode = None
        return {"ok": False, "word": "failed"}

    def _stop(self, sd, slug):
        self.calls.append("stop_app")
        self.mode = None
        return {"ok": True}

    def remove(self):
        (dc.status, dc.system_config, dc.unified_supported, dc.ensure_app,
         dc.stop_app, dc.app_state, dc.powered_off) = self.saved


notes = []


def notify(kind, title, body="", config_dir=None, **kw):
    notes.append((kind, title))


cfg_dir = tempfile.mkdtemp()
cases = [
    ("nothing running: the app is started (AI off)", Box(), "started",
     ["ensure:started"]),
    ("the unified app running, AI on: left alone", Box("unified", ai=True),
     "running", []),
    ("...AI off: left alone", Box("unified"), "running", []),
    ("an old LOGIC-mode app (a box updated from v1.0.12) is replaced once",
     Box("logic"), "replaced", ["ensure:replaced"]),
    ("an old detection app is left until its next stop", Box("detection"),
     "running", []),
    ("a workflow with nothing camera-free still gets its app",
     Box(cfg={"logic": {"functions": [], "outputs": [],
                        "version": wf.LOGIC_VERSION}}), "started",
     ["ensure:started"]),
    ("a system powered off starts nothing", Box(off=True), "powered_off", []),
    ("...and an app still running for it is stopped",
     Box("unified", off=True), "powered_off", ["stop_app"]),
    ("a start that fails says so", Box(start_ok=False), "failed",
     ["ensure:started"]),
]
for label, box, want, calls in cases:
    box.install()
    try:
        got = lr.reconcile("/x", "demo", cfg_dir, notify)
    finally:
        box.remove()
    check(label, got == want and box.calls == calls, (got, box.calls))

box = Box(supported=False).install()
try:
    first = lr.reconcile("/x", "demo", cfg_dir, notify)
    second = lr.reconcile("/x", "demo", cfg_dir, notify)
finally:
    box.remove()
check("an app copy too old for the AI switch is never started by itself",
      (first, second) == ("unsupported", "unsupported") and box.calls == [],
      (first, second, box.calls))
check("...and the admins are told once per version, not every time",
      [n for n in notes if n[0] == "app_supervisor"].__len__() == 1, notes)

# -- 5. the triggers ---------------------------------------------------------------
master = read("master_server.py")
check("master start: the watcher (first reconcile, then crash watch)",
      "app_supervisor.start_supervisor(app.state.systems_dir" in master)
check("...which waits for the hub's detection autostart first",
      lr.START_DELAY_S >= 20)
route = read("core", "routes", "workflow.py")
check("a workflow save makes sure the app is up",
      "app_supervisor.reconcile_soon(systems_dir, slug"
      in route[route.index("def _recompile"):route.index("def _variables")])
models_route = read("core", "routes", "models.py")
power = models_route[models_route.index("async def detector_power"):]
power = power[:power.index("@router.")]
check("the system page's Start/Stop flip the switch; a failure reconciles",
      "if not result.get(\"ok\"):" in power
      and power.count("app_supervisor.reconcile") == 2)
hub = read("core", "routes", "hub.py")
toggle = hub[hub.index("async def set_detection"):hub.index("async def set_autostart")]
check("System Control's switch does the same",
      "detector_control.start if on else detector_control.stop" in toggle
      and "app_supervisor.reconcile" in toggle)
power_off = hub[hub.index("def _app_power"):hub.index("def _known_slug")]
check("Hub Power off stops the WHOLE app, and brings nothing back",
      "dc.stop_app(pkg_root, slug)" in power_off
      and "app_supervisor" not in power_off)
auto = hub[hub.index("async def set_autostart"):hub.index('@router.get("/api/hub/retention"')]
check("switching autostart ON ends a Hub Power off, before it starts the system "
      "(else the toggle did nothing and the system stayed down after a reboot)",
      "set_powered_off(" in auto
      and auto.index("set_powered_off(") < auto.index("provision_service.autostart_system"))
prov = read("core", "services", "provision.py")
check("...a deleted system too", "dc.stop_app(systems_dir, slug)" in prov
      and "app_supervisor" not in prov)
ret = read("core", "services", "retention.py")
check("...and a counters reset, under the app lock, bringing back what ran",
      "dc.stop_app(systems_dir, slug)" in ret
      and "with app_lock.held(slug):" in ret)

# -- 6. crash watch -------------------------------------------------------------------
lr._crashes.clear()
lr._paused.clear()
box = Box("unified").install()
try:
    box.raw = {"running": False, "mode": "unified"}     # a clean stop
    check("a running app: nothing to do", lr.watch_step(
        "/x", "demo", cfg_dir, 1000, notify) == "none")
    box.mode = None
    check("a clean stop while online: the app is brought back, not counted",
          lr.watch_step("/x", "demo", cfg_dir, 1000, notify) == "started"
          and lr._crashes.get("demo") in (None, []), lr._crashes)
    box.raw = {"running": True, "mode": "unified", "ai_on": False,
               "pid": 999999}
    got = []
    for i in range(3):
        box.mode = None                 # it keeps dying
        got.append(lr.watch_step("/x", "demo", cfg_dir, 1000 + i, notify))
    check("a crashed app is restarted",
          got[:2] == ["restarted", "restarted"]
          and box.calls.count("ensure:started") >= 3, (got, box.calls))
    check("...until it has crashed 3 times, then it backs off and says so",
          got[2] == "backoff"
          and any("keeps stopping" in n[1] for n in notes), (got, notes))
    check("...and waits out the back-off",
          lr.watch_step("/x", "demo", cfg_dir, 1100, notify) == "paused")
    lr._paused.clear()
    lr._crashes.clear()
    notes.clear()
    box.raw = {"running": True, "mode": "unified", "ai_on": True,
               "pid": 999999}
    box.mode = None
    got = lr.watch_step("/x", "demo", cfg_dir, 5000, notify)
    check("an app that died DETECTING comes back with the AI OFF, and says so",
          got == "restarted" and box.mode == "unified" and box.ai is False
          and any("Detection stopped" in n[1] for n in notes), (got, notes))
    lr._crashes.clear()
    notes.clear()
    box.raw = {"running": True, "mode": "detection", "pid": 999999}
    box.mode = None
    got = lr.watch_step("/x", "demo", cfg_dir, 6000, notify)
    check("...an old detection app that died too (the unified app takes over)",
          got == "restarted" and box.mode == "unified" and box.ai is False,
          (got, box.calls))
finally:
    box.remove()

# == review fixes (Yaya, after 37367cb) =====================================


def agree(g, out, label):
    """The canvas and logic mode must say the SAME thing about every drawn
    node that compiles to a function: tagged "waits" exactly when logic mode
    skips its function (one decision, workflow.logic_artifacts)."""
    free = set(out["frames_free"]["functions"])
    waits = set(out["waits_for_detection"])
    fids = {f["id"] for f in out["functions"]}
    wrong = []
    for n in g["nodes"]:
        fid_ = (n.get("data") or {}).get("fnId")
        if fid_ in fids and ((n["id"] in waits) == (fid_ in free)):
            wrong.append((n["id"], fid_, n["id"] in waits, fid_ in free))
    check(f"{label}: the canvas says what logic mode does", not wrong, wrong)


# HIGH 1 -- a stale config (compiled before [logic]) must never fire on a key
# nobody publishes. Yaya's probe_stale: "(people < 1)" + an email.
STALE = {"functions": [
    {"id": "people_cnt", "type": "frame_count", "params": {"classes": ["person"]}},
    {"id": "empty_x", "type": "expr", "params": {"expr": "(fn_people_cnt_count < 1)"},
     "trigger": {"op": "==", "value": 1}},
    {"id": "kick", "type": "inject", "params": {}},
    {"id": "dbl", "type": "expr", "params": {"expr": "fn_kick_value * 2"}}],
    "outputs": [{"id": "wf_mail", "type": "email", "function": "empty_x",
                 "trigger": "alert"},
                {"id": "wf_mq", "type": "mqtt", "function": "kick",
                 "trigger": "alert"},
                {"id": "wf_pub", "type": "mqtt_publish", "key": "zone_bay_count"}]}
lc = apprun.ai_config({**json.loads(json.dumps(STALE)), "ai": {"on": False}})
check("R1 a stale config: the Formula over an unpublished count does not run",
      lc["_ai_inactive"] == ["empty_x", "people_cnt"], lc["_ai_inactive"])
check("R1 ...nor the email it would fire, nor an output reading a camera key",
      [o["id"] for o in lc["outputs"]] == ["wf_mq"], lc["outputs"])
lc = apprun.ai_config({**json.loads(json.dumps(STALE)), "ai": {"on": False},
                       "logic": {"functions": ["empty_x", "kick"],
                                 "outputs": ["wf_mail"]}})
check("R1 ...and even a LIST naming it cannot make a 0 fire an alarm",
      "empty_x" in lc["_ai_inactive"] and "kick" not in lc["_ai_inactive"]
      and lc["outputs"] == [], (lc["_ai_inactive"], lc["outputs"]))
saved_cfg = dc.system_config
dc.system_config = lambda sd, slug: STALE
try:
    check("R1 a stale config is reported as such", lr.logic_stale("/x", "demo"))
finally:
    dc.system_config = saved_cfg
sup = read("core", "services", "app_supervisor.py")
start_body = sup[sup.index("def start_supervisor"):]
check("R1 the master recompiles a stale workflow at start, then reconciles",
      start_body.index("ensure_compiled(") < start_body.index("reconcile(")
      and "_recompile(systems_dir, config_dir, slug, store)" in sup)

# The compile side of the same rule: a branch over a Python node that did
# not compile (unpublished key) waits -- Yaya's probe_reach P1.
nodes = [node("py", "analytics.python", name="People"),
         node("br", "branch.cases", rules=[{"op": "<", "value": 1, "input": "in1"}]),
         node("mq", "output.mqtt")]
g, out = compile_(nodes, [edge("py", "br", th="in1"), edge("br", "mq", sh="r0")])
check("R1 a branch over a key nobody publishes waits",
      out["frames_free"]["functions"] == []
      and "br" in out["waits_for_detection"], out["frames_free"])
agree(g, out, "R1")

# MED-HIGH 3 -- topics, names and labels are TEXT, not keys (probe_topic,
# probe_name).
nodes = [node("sub", "variable.mqtt", name="Line state", topic="plant/line_2/state"),
         node("inj", "common.inject", name="Line kick", ptype="number", payload="1"),
         node("half", "math.arith", name="Zone half", op="div", b_value=2),
         node("pub", "output.mqtt_publish", topic="plant/line_1/count"),
         node("mq", "output.mqtt", topic="plant/zone_a/alert"),
         node("card", "dashboard.card", name="State")]
edges = [edge("inj", "half", th="a"), edge("half", "pub"), edge("inj", "mq"),
         edge("sub", "card")]
g, out = compile_(nodes, edges)
check("R3 the test graph compiles", not out["errors"], out["errors"])
free = out["frames_free"]
check("R3 an MQTT Subscribe on plant/line_2/state runs",
      fid(g, "sub") in free["functions"], free)
check("R3 an Inject NAMED 'Line kick' runs, and the Math NAMED 'Zone half'",
      fid(g, "inj") in free["functions"] and fid(g, "half") in free["functions"],
      free)
check("R3 outputs on topics with line_/zone_ in them run",
      sorted(free["outputs"]) == ["wf_mq", "wf_pub"], free)
check("R3 ...and the canvas marks none of them",
      out["waits_for_detection"] == [], out["waits_for_detection"])
agree(g, out, "R3")

# The canvas follows the SAME decision where the wires alone would not: a
# Math reading a Variable that a detection count writes (probe_reach P3).
nodes = [node("cam", "source.detection"),
         node("cnt", "analytics.frame", name="People", mode="realtime"),
         node("v", "variable.number", name="Occ", value=0),
         node("set", "math.setvar", name="SetOcc"),
         node("inj", "common.inject", name="Kick", ptype="number", payload="1"),
         node("rd", "math.arith", name="ReadOcc", op="add"),
         node("mq", "output.mqtt")]
edges = [edge("cam", "cnt"), edge("v", "set", th="a"), edge("cnt", "set", th="b"),
         edge("v", "rd", th="a"), edge("inj", "rd", th="b"), edge("rd", "mq")]
g, out = compile_(nodes, edges)
check("R3 a Math reading a Variable a count writes waits -- on the canvas too",
      fid(g, "rd") not in out["frames_free"]["functions"]
      and "rd" in out["waits_for_detection"]
      and "mq" in out["waits_for_detection"], out["waits_for_detection"])
agree(g, out, "R3 variable")

# LOW: a chain of Link jumps is relayed to a fixpoint.
nodes = [node("cam", "source.detection"),
         node("cnt", "analytics.frame", name="People", mode="realtime")]
edges = [edge("cam", "cnt")]
prev = "cnt"
for i in range(5):
    nodes += [node(f"lo{i}", "common.link_out", link=f"j{i}"),
              node(f"li{i}", "common.link_in", link=f"j{i}"),
              node(f"m{i}", "math.arith", name=f"Hop {i}", op="mul", b_value=2)]
    edges += [edge(prev, f"lo{i}"), edge(f"li{i}", f"m{i}", th="a")]
    prev = f"m{i}"
g, out = compile_(nodes, edges)
waits = set(out["waits_for_detection"])
check("LOW five Link jumps in a row are all marked",
      all({f"lo{i}", f"li{i}", f"m{i}"} <= waits for i in range(5)),
      sorted(waits))
agree(g, out, "LOW links")

# HIGH 2 -- Power off is remembered, and nothing brings the system back.
pkg2 = tempfile.mkdtemp()
real_pkg = dc._pkg_dir
dc._pkg_dir = lambda sd, slug: pkg2
try:
    dc.set_powered_off("/x", "demo", True)
    off1 = dc.powered_off("/x", "demo")
    dc.set_powered_off("/x", "demo", False)
    off2 = dc.powered_off("/x", "demo")
finally:
    dc._pkg_dir = real_pkg
check("R2 Power off is remembered in the system's config, Power on clears it",
      (off1, off2) == (True, False), (off1, off2))
from core.services import provision as provision_service       # noqa: E402
saved_off = dc.powered_off
dc.powered_off = lambda sd, slug: True
try:
    res = provision_service.autostart_system("/x", "demo", {"url": ""}, ROOT)
finally:
    dc.powered_off = saved_off
check("R2 the boot autostart leaves a powered-off system down",
      "powered off" in res["message"] and res["app"] == "skipped", res)
box = Box(off=True).install()
try:
    box.raw = {"running": True, "mode": "unified", "pid": 999999}
    check("R2 the supervisor does not restart a powered-off system's app",
          lr.watch_step("/x", "demo", cfg_dir, 9000, notify) == "none"
          and box.calls == [], box.calls)
finally:
    box.remove()
hub_src = read("core", "routes", "hub.py")
pw = hub_src[hub_src.index("async def power_system"):hub_src.index("def _app_power")]
check("R2 the Hub's power button writes the flag BEFORE its early return",
      pw.index("set_powered_off(pkg_root, slug, action == \"stop\")")
      < pw.index("master already running"))

# MED 4 -- the start race.
rt_src = read("systems", "demo", "app", "runtime.py")
start_src = rt_src[rt_src.index("def start(pkg_dir, data_dir"):rt_src.index("def stop(")]
check("R4 the pid and mode are recorded BEFORE the survival wait",
      start_src.index("error=None, mode=mode, detector=None)")
      < start_src.index("deadline = time.time() + SURVIVE_S"))
check("R4 a running app from before the switch is refused, not reported "
      "started", "stop it before starting" in start_src)
check("R4 the old logic-mode switch is not handed to the child",
      'env.pop("CYTRON_APP_MODE", None)' in start_src)
from app import runtime as app_runtime                          # noqa: E402
rt_state = {"mode": "logic", "running": False}
killed = []
saved_rt = (app_runtime.status, app_runtime._stray_app_pids, app_runtime.read,
            app_runtime.write, app_runtime.os.kill, app_runtime._alive,
            app_runtime.subprocess.Popen)


class _Proc:
    pid = 4242

    def poll(self):
        return None


app_runtime.status = lambda d, p=None: {"running": False}
app_runtime._stray_app_pids = lambda p: [777]
app_runtime.read = lambda d: dict(rt_state)
app_runtime.write = lambda d, **kw: rt_state.update(kw)
app_runtime.os.kill = lambda pid, sig: killed.append(pid)
app_runtime._alive = lambda pid: False
app_runtime.subprocess.Popen = lambda *a, **k: _Proc()
try:
    ok, msg = app_runtime.start(tempfile.mkdtemp(), tempfile.mkdtemp())
finally:
    (app_runtime.status, app_runtime._stray_app_pids, app_runtime.read,
     app_runtime.write, app_runtime.os.kill, app_runtime._alive,
     app_runtime.subprocess.Popen) = saved_rt
check("R4 an old logic-mode stray is stopped, never re-adopted",
      killed == [777] and "re-adopted" not in msg
      and rt_state["mode"] == "unified", (killed, msg, rt_state))
check("R4 the runtime says it has the switch", app_runtime.UNIFIED is True
      and not hasattr(app_runtime, "LOGIC_MODE"))
dc_src = read("core", "services", "detector_control.py")
check("R4 start, stop, stop_app and ensure_app hold the app lock",
      # start takes require_camera after the two (the page's Start probes
      # the camera; unwatched Starts pass False); the wrapper forwards it.
      all(f"@_app_locked\ndef {name}(systems_dir, slug{tail}" in dc_src
          for name, tail in (("start", ", require_camera=True):"),
                             ("stop", "):"), ("stop_app", "):"),
                             ("ensure_app", "):")))
      and "def locked(systems_dir, slug, *args, **kwargs):" in dc_src
      and "def start_logic" not in dc_src)
check("R4 ...and so does reconcile, look and act together",
      "with app_lock.held(slug):" in sup)
from core.services import app_lock                             # noqa: E402
with app_lock.held("demo"):
    with app_lock.held("demo"):
        nested = True
check("R4 the app lock is re-entrant (Start replaces an old app under it)",
      nested)

# LOW -- nothing starts while an update swaps the release.
from core.services import update as update_service              # noqa: E402
saved_m = update_service.maintenance
update_service.maintenance = lambda *a, **k: True
box = Box().install()
try:
    check("LOW nothing starts during an update",
          lr.reconcile("/x", "demo", cfg_dir, notify) == "maintenance"
          and box.calls == [], box.calls)
finally:
    box.remove()
    update_service.maintenance = saved_m

# == round 2 (Yaya, after 5bb48e4 / 35c001a) ================================

# 2 -- an Inject press brings the app up through the SAME guards.
box = Box(off=True).install()
try:
    word, why = lr.start_for_press("/x", "demo")
finally:
    box.remove()
check("RR2 a press on a powered-off system is refused, and says why",
      word == "powered_off" and "powered off" in why and box.calls == [],
      (word, why, box.calls))
saved_m = update_service.maintenance
update_service.maintenance = lambda *a, **k: True
box = Box().install()
try:
    word, why = lr.start_for_press("/x", "demo")
finally:
    box.remove()
    update_service.maintenance = saved_m
check("RR2 ...and during an update",
      word == "maintenance" and box.calls == [], (word, box.calls))
box = Box(cfg={"functions": [{"id": "kick", "type": "inject"}]}).install()
try:
    word, why = lr.start_for_press("/x", "demo")
finally:
    box.remove()
check("RR2 a press on a stale config still starts (the app prunes it)",
      word == "started" and box.calls == ["ensure:started"], (word, box.calls))
route = read("core", "routes", "workflow.py")
press = route[route.index("async def inject(request"):route.index("def _send_one")]
check("RR2 the Inject route goes through start_for_press",
      "app_supervisor.start_for_press(systems_dir, slug)" in press
      and "start_logic(" not in press)

# 3 -- an explicit start ends Power off ONLY when it goes through; the Hub
# says "Powered off". This check used to drive a start that was refused (no
# camera) and expect the flag cleared anyway -- which was the defect: the
# refusal ended the Power off and the route's error path then had the
# supervisor bring the workflow up for a switched-off system. A start that
# gets as far as the app clears it (smoke_aiboot covers that path end to end).
pkg3 = tempfile.mkdtemp()
real_pkg = dc._pkg_dir
dc._pkg_dir = lambda sd, slug: pkg3
saved_cams = dc.cameras
dc.cameras = lambda sd, slug: []            # the start itself is refused...
try:
    dc.set_powered_off("/x", "demo", True)
    try:
        dc.start("/x", "demo")
    except ValueError:
        pass                                # ...no camera, as intended
    still_off = dc.powered_off("/x", "demo")
finally:
    dc._pkg_dir = real_pkg
    dc.cameras = saved_cams
check("RR3 a Start refused for no camera leaves the Hub Power off standing",
      still_off is True, still_off)
hub = read("core", "routes", "hub.py")
check("RR3 System Control shows 'Powered off' while the flag is set",
      'state = "powered_off"' in hub
      and 'label: "Powered off"' in read("frontend", "src", "components",
                                         "ControlPanel.jsx"))

# 4 -- text in an expression is not a key; another canvas's rule is a rule.
docs = [{"id": "kick", "type": "inject", "params": {}},
        {"id": "msg", "type": "expr",
         "params": {"expr": 'iff(fn_kick_value > 0, "line_1 stopped", \'zone_a ok\')'}}]
check("RR4 quoted text with line_/zone_ in an expression is not a key",
      wf.logic_artifacts(docs, [], set(), {}, {}, {}, [])["functions"]
      == ["kick", "msg"])
kept, _o = apprun.logic_prune(docs, [])
check("RR4 ...on the app side too", [f["id"] for f in kept] == ["kick", "msg"],
      kept)
gA = {"nodes": [node("inj", "common.inject", name="Kick", ptype="number",
                     payload="1")], "edges": []}
gB = {"nodes": [node("rdv", "variable.ref", name="K", io="in",
                     key="fn_kick_inj_value"),
                node("dbl", "math.arith", name="Dbl", op="mul", b_value=2),
                node("pub", "output.mqtt_publish", topic="plant/x")],
      "edges": [edge("rdv", "dbl", th="a"), edge("dbl", "pub")]}
for gg in (gA, gB):
    g2 = wf.clean_graph(gg)
    wf.ensure_fn_ids(g2)
    gg.update(g2)
store = {"workflows": [{"id": "wa", "name": "A", "graph": gA},
                       {"id": "wb", "name": "B", "graph": gB}]}
out = wf.compile_store(store, [], ["person"], ["person"])
check("RR4 a canvas reading ANOTHER canvas's Inject runs without detection",
      "dbl_dbl" in out["frames_free"]["functions"]
      and "wf_pub" in out["frames_free"]["outputs"], out["frames_free"])
check("RR4 ...and neither canvas marks it as waiting",
      out["waits_for_detection"] == [], out["waits_for_detection"])
gC = {"nodes": [node("cam", "source.detection"),
                node("cnt", "analytics.frame", name="People", mode="realtime")],
      "edges": [edge("cam", "cnt")]}
gD = {"nodes": [node("rdv", "variable.ref", name="P", io="in",
                     key="fn_people_cnt_count"),
                node("dbl", "math.arith", name="Dbl", op="mul", b_value=2)],
      "edges": [edge("rdv", "dbl", th="a")]}
for gg in (gC, gD):
    g2 = wf.clean_graph(gg)
    wf.ensure_fn_ids(g2)
    gg.update(g2)
out = wf.compile_store({"workflows": [{"id": "wc", "name": "C", "graph": gC},
                                      {"id": "wd", "name": "D", "graph": gD}]},
                       [], ["person"], ["person"])
check("RR4 ...while one reading another canvas's COUNT still waits, and is marked",
      "dbl_dbl" not in out["frames_free"]["functions"]
      and "dbl" in out["waits_for_detection"], (out["frames_free"],
                                                out["waits_for_detection"]))

# -- the canvas and the hub say so -------------------------------------------------------
page = read("frontend", "src", "pages", "Workflow.jsx")
# Tok Aba 2026-10-14: the "workflow running (detection off)" badge is gone from the
# Workflow page; the red "AI Detection" label (AiDot) says detection is off.
check("the Workflow page no longer shows the 'workflow running (detection off)' badge",
      '<Badge tone="ok">workflow running (detection off)</Badge>' not in page
      and 'runtimeMode === "logic"' not in page)
# Tok Aba 2026-10-05: the canvas does NOT mark the fed nodes (no dimming, no
# "waits for detection" tag); they look normal and the AI dot says detection
# is off. The runtime still holds them back (smoke_aiswitch). The one reader
# allowed to say it is the Debug PROBE's readout (DebugReadout), which would
# otherwise say "nothing published yet" for ever on a held node (Tok Aba, the
# same day, v1.0.16): so the pin is on the node chrome (NodeShell), the
# dimming (offNodeIds) and the badge, not on the identifier anywhere.
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
shell = nodes_src.split("function NodeShell(", 1)[1].split("\nconst SourceNode", 1)[0]
check("...and marks no node: the fed nodes look normal with detection off",
      "waitsForDetection" not in shell and "waits for detection" not in shell
      and "waitsForDetection" not in page.split("function offNodeIds", 1)[1]
                                          .split("\nexport default function", 1)[0])
check("...and the badge tooltip does not say anything waits",
      "rest waits for detection" not in page
      # The badge itself is gone (319802a3); its tooltip went with it.
      and "Detection is off: no camera or AI model is running." not in page)
check("the System Control table does not say it either",
      "workflow running (detection off)" not in read(
          "frontend", "src", "components", "ControlPanel.jsx")
      and '"logic_running": bool(stat.get("logic_running"))' in hub)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all logic-mode checks passed")
