# Yaya's review of 401b7b9 (the one-app AI switch), pinned.
#
#   1. AI off publishes NOTHING a camera measures (zone / object / violation
#      / compliance keys, and their per-camera copies): history records a
#      gap, and a card bound to one says "AI off" -- never 0, and not "no
#      data yet" either. Outputs fed by them do not send.
#   2. The green dot needs a FRESH heartbeat (a stalled app is red, "not
#      responding") and EVERY camera producing (red names the stale one).
#   3. A detection-fed Running Total survives an off/on flip, and a held
#      alarm on a detection-fed rule does not fire again when it rejoins.
#   4. (moved: smoke_cfgwriters runs the cross-process config race)
#   5. A removed system stays down: the removal powers it off first, and a
#      master refuses a built-in system the Hub no longer enables.
#   6. The gstreamer kind follows the switch both ways without opening a
#      camera, and AI off there reads "AI detection is off", not a camera
#      fault.
# The app loop runs here for real, with counting fakes for the camera, the
# detector and the gstreamer pipeline (no board: .47 is away).
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

import numpy as np                                             # noqa: E402

from app import detector as detector_mod                       # noqa: E402
from app import run as apprun                                  # noqa: E402
from app import runtime as app_runtime                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- fakes -------------------------------------------------------------------------
RealCamera = apprun.camera_mod.Camera
CAM = {"opens": 0, "live": set(), "dead": set()}


class FakeCamera(RealCamera):
    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))
        self._cid = str(config.get("id") or "")

    def open(self):
        CAM["opens"] += 1
        CAM["live"].add(id(self))
        return super().open()

    def close(self):
        CAM["live"].discard(id(self))
        return super().close()

    def read(self):
        if self._cid in CAM["dead"]:
            return None
        return super().read()


DET = {"builds": 0, "live": 0, "people": True}


class FakeChip(detector_mod.Detector):
    name = "fakechip"

    def __init__(self, config):
        super().__init__(config)
        DET["builds"] += 1
        DET["live"] += 1
        self._open = True

    def detect(self, frame):
        if not DET["people"]:
            return []
        return [{"label": "person", "confidence": 0.9, "box": [20, 20, 120, 200]}]

    def close(self):
        if self._open:
            self._open = False
            DET["live"] -= 1


GST = {"builds": 0, "live": 0}


class FakeGst(detector_mod.Detector):
    """The gstreamer pipeline: owns the camera AND the model."""
    name = "gstreamer"
    error = None
    infer_fps = {}

    def __init__(self):
        super().__init__({})
        GST["builds"] += 1
        GST["live"] += 1
        self._open = True

    def latest(self):
        return (np.zeros((240, 320, 3), dtype=np.uint8),
                [{"label": "person", "confidence": 0.9,
                  "box_norm": [0.1, 0.1, 0.5, 0.9]}])

    def close(self):
        if self._open:
            self._open = False
            GST["live"] -= 1


def fake_build_gst(det_cfg, cam_cfg):
    g = FakeGst()
    return g, g, None


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._build_gst = fake_build_gst
apprun._CONFIG_POLL_S = 0.02


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Run:
    """One in-process run of the app loop over a config the test rewrites."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cfg.setdefault("app", {}).update(stream_port=free_port(),
                                              stream_bind="127.0.0.1")
        self.data = tempfile.mkdtemp()
        self.mt = [1.0]

    def set(self, **kw):
        self.cfg.update(kw)
        self.mt[0] += 1.0

    def state(self):
        try:
            with open(os.path.join(self.data, "state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def counts(self):
        return self.state().get("counts") or {}

    def fires(self, word):
        try:
            with open(os.path.join(self.data, "events.jsonl"), encoding="utf-8") as f:
                return sum(1 for line in f if word in line)
        except OSError:
            return 0

    def go(self, drive):
        apprun._stop.clear()
        apprun.config_mod.load = lambda *a, **k: copy.deepcopy(self.cfg)
        apprun.config_mod.data_dir = lambda *a, **k: self.data
        apprun._config_mtime = lambda: self.mt[0]

        def wrapped():
            try:
                drive(self)
            except Exception as exc:                            # noqa: BLE001
                check("the driver ran", False, repr(exc))
            finally:
                apprun._stop.set()

        t = threading.Thread(target=wrapped, daemon=True)
        t.start()
        rc = apprun.main([])
        t.join(5)
        return rc


def wait(pred, limit=15.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.05)
    return False


ZONE = {"id": "bay", "name": "Bay", "kind": "area", "camera": "cam1",
        "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}
BASE = {
    "cameras": [{"id": "cam1", "source": "rtsp://10.0.0.9/a", "enabled": True,
                 "fps": 10, "width": 320, "height": 240}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": [ZONE],
    "outputs": [],
    "logic": {"functions": ["kick"], "outputs": [], "version": 99},
    "functions": [
        {"id": "kick", "name": "Kick", "type": "inject", "enabled": True,
         "params": {"ptype": "number", "payload": "1", "interval_s": 0.3}},
        {"id": "ppl", "name": "People", "type": "frame_count", "enabled": True,
         "params": {},
         "triggers": [{"metric": "count", "op": ">=", "value": 1,
                       "severity": "warning", "cooldown_s": 0,
                       "message": "people alarm"}]},
        {"id": "ptot", "name": "Arrivals", "type": "total", "enabled": True,
         "params": {"key": "fn_ppl_count"}}],
}
FRAME_KEYS = ("zone_bay_count", "objects_in_frame", "violations_now",
              "cam_cam1_zone_bay_count", "cam_cam1_objects",
              "cam_cam1_violations")

# -- 1 + 3: AI off publishes nothing measured; totals and alarms carry on ----------
r1 = Run(dict(copy.deepcopy(BASE), ai={"on": False}))
seen = {}


def drive1(r):
    ok = wait(lambda: r.counts().get("fn_kick_runs", 0) >= 2)
    c = r.counts()
    check("1 AI off: the free rules publish", ok, c)
    check("1 ...no zone / object / violation key at all (not 0)",
          not [k for k in FRAME_KEYS if k in c], sorted(c))
    check("1 ...the free rule's per-camera copy is kept",
          "cam_cam1_fn_kick_runs" in c, sorted(c))
    check("1 ...and the state names the rules sitting out",
          sorted(r.state().get("ai_inactive") or []) == ["ppl", "ptot"],
          r.state().get("ai_inactive"))
    seen["off_counts"] = c
    r.set(ai={"on": True})
    wait(lambda: r.state().get("ai_ok") is True)
    wait(lambda: r.counts().get("zone_bay_count") == 1, 5)
    check("1 AI on: the zone count is back", r.counts().get("zone_bay_count") == 1,
          sorted(r.counts()))
    # Two arrivals: nobody, somebody, nobody, somebody.
    for _ in range(2):
        DET["people"] = False
        wait(lambda: r.counts().get("fn_ppl_count") == 0, 5)
        DET["people"] = True
        wait(lambda: r.counts().get("fn_ppl_count") == 1, 5)
    wait(lambda: r.counts().get("fn_ptot_total", 0) >= 2, 5)
    before = r.counts().get("fn_ptot_total", 0)
    alarms = r.fires("people alarm")
    check("3 a detection-fed Running Total counts", before >= 2, r.counts())
    r.set(ai={"on": False})
    wait(lambda: r.state().get("ai_on") is False and DET["live"] == 0)
    time.sleep(0.5)
    c = r.counts()
    check("1 AI off again: nothing live is published",
          not [k for k in FRAME_KEYS if k in c]
          and "fn_ppl_count" not in c, sorted(c))
    check("1 ...but the detection-fed Running Total's KEPT number is, as it "
          "stands", c.get("fn_ptot_total") == before, (before, c))
    kept = r.state().get("ai_kept") or []
    check("1 ...named as held, not live (and its per-camera copy)",
          "fn_ptot_total" in kept and "cam_cam1_fn_ptot_total" in kept, kept)
    seen["kept_counts"], seen["kept"] = dict(c), list(kept)
    r.set(ai={"on": True})               # the person is still there
    wait(lambda: r.state().get("ai_ok") is True)
    wait(lambda: "fn_ptot_total" in r.counts(), 5)
    time.sleep(1.0)
    after = r.counts().get("fn_ptot_total", 0)
    check("3 ...the Running Total survives the flip (no reset to 0, no "
          "phantom rise)", after == before, (before, after))
    check("3 ...and the held alarm does not fire again on rejoin",
          r.fires("people alarm") == alarms, (alarms, r.fires("people alarm")))


check("the app ran", r1.go(drive1) == 0)

# History records a gap, not a zero.
from core.services import history as history_service           # noqa: E402
history_service._loaded = True
history_service._last_sample = 0
history_service.sample(dict(seen.get("off_counts") or {}), now=time.time())
check("1 history samples no zone key while the AI is off (a gap)",
      "zone_bay_count" not in history_service._fine
      and "fn_kick_runs" in history_service._fine, sorted(history_service._fine))

# The cards say "AI off".
from core.services import builder                              # noqa: E402
from core.services import detector_control as dc               # noqa: E402

stats = dict(seen.get("off_counts") or {}, events_total=4, violations_today=1)
stats.update({k: v for k, v in (seen.get("kept_counts") or {}).items()
              if k.startswith("fn_ptot_")}, fn_ppl_extra_count=3)
saved = (dc.status, dc.app_state, dc.system_config)
dc.status = lambda sd, slug: {"logic_running": True, "running": False}
dc.app_state = lambda sd, slug: {"ai_inactive": ["ppl", "ptot"],
                                 "ai_kept": ["fn_ptot_total"]}
dc.system_config = lambda sd, slug: {
    "functions": BASE["functions"] + [{"id": "ppl_extra", "type": "expr"}],
    "cameras": BASE["cameras"]}
try:
    W = [{"id": "t1", "title": "Bay", "kind": "tile", "source": "stat",
          "key": "zone_bay_count"},
         {"id": "t2", "title": "People", "kind": "tile", "source": "stat",
          "key": "fn_ppl_count"},
         {"id": "t3", "title": "Kicks", "kind": "tile", "source": "stat",
          "key": "fn_kick_runs"},
         {"id": "t4", "title": "Events", "kind": "tile", "source": "stat",
          "key": "events_total"},
         {"id": "v1", "title": "Compliance", "kind": "verdict",
          "source": "stat", "key": ""},
         {"id": "s1", "title": "Crowd", "kind": "status", "source": "stat",
          "key": "objects_in_frame", "status": {"op": ">=", "value": 3}},
         {"id": "t5", "title": "Never", "kind": "tile", "source": "stat",
          "key": "fn_nothing_yet_value"},
         {"id": "k1", "title": "Arrivals", "kind": "tile", "source": "stat",
          "key": "fn_ptot_total"},
         {"id": "x1", "title": "Extra", "kind": "tile", "source": "stat",
          "key": "fn_ppl_extra_count"},
         {"id": "m1", "title": "Mix", "kind": "bar", "source": "stat",
          "series": [{"id": "a", "source": "stat", "key": "fn_kick_runs",
                      "label": "Kicks"},
                     {"id": "b", "source": "stat", "key": "zone_bay_count",
                      "label": "Bay"}]}]
    got = {w["id"]: w["data"] for w in builder.compute(
        W, [], stats, config_dir=tempfile.mkdtemp(), slug="demo",
        systems_dir="/x")}
finally:
    dc.status, dc.app_state, dc.system_config = saved
check("1 a tile on a zone count says AI off", got["t1"].get("ai_off") is True,
      got["t1"])
check("1 ...a tile on a detection-fed rule too", got["t2"].get("ai_off") is True,
      got["t2"])
check("1 ...a free rule's tile does not", not got["t3"].get("ai_off"), got["t3"])
check("1 ...nor the master's own event counters",
      not got["t4"].get("ai_off"), got["t4"])
check("1 ...the compliance verdict says AI OFFLINE, not NO DATA",
      got["v1"].get("ai_off") and got["v1"].get("label") == "AI OFFLINE", got["v1"])
check("1 ...a status card is not an 'OK' read off a missing 0",
      got["s1"].get("ai_off") and got["s1"].get("label") == "AI OFFLINE"
      and got["s1"].get("state") != "ok", got["s1"])
check("1 ...and a key nothing publishes yet stays 'no data yet'",
      not got["t5"].get("ai_off"), got["t5"])
check("3' a kept tally shows its REAL number with an AI off badge",
      got["k1"].get("value") == seen["kept_counts"].get("fn_ptot_total")
      and got["k1"].get("ai_badge") and not got["k1"].get("ai_off")
      and got["k1"].get("ai_kept_keys") == ["fn_ptot_total"], got["k1"])
check("4 exact ownership: rule 'ppl' sitting out does not claim "
      "'fn_ppl_extra_count' (rule 'ppl_extra')",
      not got["x1"].get("ai_badge") and not got["x1"].get("ai_off")
      and got["x1"].get("value") == 3, got["x1"])
check("4 ...and the owner is the LONGEST id",
      builder.key_owner("fn_ppl_extra_count", {"ppl", "ppl_extra"}) == "ppl_extra"
      and builder.key_owner("cam_cam1_fn_ppl_count", {"ppl", "ppl_extra"},
                            {"cam1"}) == "ppl")
m1 = got["m1"]
check("4 a card mixing a free key and a detection key keeps the free value "
      "and marks only the other",
      not m1.get("ai_off") and m1.get("ai_badge")
      and m1.get("ai_off_keys") == ["zone_bay_count"]
      and m1.get("values") and m1["values"][0] is not None
      and m1["values"][1] is None and "(AI OFFLINE)" in m1["labels"][1], m1)

# The history gap holds for kept tallies too.
ms_src = read("master_server.py")
check("3' history never samples a kept tally (the gap stays a gap)",
      "history_service.sample(_live_only(app, stats))" in ms_src
      and '.get("ai_kept")' in ms_src)
wjsx = read("frontend", "src", "components", "widgets", "Widget.jsx")
check("the badge is drawn whenever the card keeps real numbers",
      "data?.ai_badge &&" in wjsx and "data.ai_off && !data.ai_badge" in wjsx)
# Worded AI OFFLINE on cards and tiles, badge included (Tok Aba
# 2026-10-05), and the sub-line no longer says it "waits".
check("1 the card draws the AI off state (greyed, 'AI OFFLINE', never 0)",
      "data.ai_off" in wjsx and ">AI OFFLINE<" in wjsx
      and ">AI off<" not in wjsx and "waits for AI detection" not in wjsx)
check("1 ...the kept-tally badge says AI OFFLINE too",
      wjsx.count(">AI OFFLINE<") == 2, wjsx.count(">AI OFFLINE<"))
outs = apprun.standalone({"functions": BASE["functions"],
                          "logic": {"functions": ["kick"], "outputs": ["wf_bl", "wf_ok"]},
                          "outputs": [{"id": "wf_bl", "type": "mqtt_publish",
                                       "key": "zone_bay_count"},
                                      {"id": "wf_ok", "type": "mqtt_publish",
                                       "key": "fn_kick_value"}]})[1]
check("1 an output fed by a camera key does not send with the AI off",
      [o["id"] for o in outs] == ["wf_ok"], outs)

# -- 2: the dot needs a fresh heartbeat, and every camera -------------------------
now = time.time()
st = {"running": True, "mode": "unified", "ai_on": True, "ai_ok": True,
      "last_heartbeat": now - 25, "stalled": False}
check("2 a stale heartbeat is not green, whatever ai_ok last said",
      dc._ai_ok(st, True) is False
      and "not responding" in dc._ai_reason(st, True, True),
      dc._ai_reason(st, True, True))
st.update(last_heartbeat=now, stalled=True)
check("2 ...a stalled app neither", dc._ai_ok(st, True) is False
      and "not responding" in dc._ai_reason(st, True, True))
st.update(stalled=False)
check("2 ...a fresh one is", dc._ai_ok(st, True) is True)
tmpd = tempfile.mkdtemp()
app_runtime.write(tmpd, pid=os.getpid(), running=True, ai_ok=True, ai_on=True)
with open(os.path.join(tmpd, "state.json"), encoding="utf-8") as f:
    raw = json.load(f)
raw["heartbeat"] = time.time() - 15
with open(os.path.join(tmpd, "state.json"), "w", encoding="utf-8") as f:
    json.dump(raw, f)
app_runtime._read_cache.clear()
check("2 the app's runtime.status drops ai_ok on an old heartbeat",
      app_runtime.status(tmpd)["ai_ok"] is False, app_runtime.status(tmpd))

r2 = Run(dict(copy.deepcopy(BASE), ai={"on": True}, zones=[],
              cameras=[dict(BASE["cameras"][0], name="Entrance"),
                       dict(BASE["cameras"][0], id="cam2", name="Dock")]))


def drive2(r):
    green = wait(lambda: r.state().get("ai_ok") is True)
    check("2 two cameras, both producing: green", green, r.state())
    CAM["dead"].add("cam2")
    red = wait(lambda: r.state().get("ai_ok") is False, 10)
    err = str(r.state().get("ai_error"))
    check("2 one camera stops giving frames: red, naming it",
          red and "(Dock)" in err and "Entrance" not in err, err)
    CAM["dead"].clear()
    check("2 ...green again when it comes back",
          wait(lambda: r.state().get("ai_ok") is True, 10), r.state())


check("the two-camera app ran", r2.go(drive2) == 0)

# -- 6: gstreamer, both directions ------------------------------------------------
CAM["opens"] = 0
CAM["live"].clear()
r3 = Run(dict(copy.deepcopy(BASE), ai={"on": False}, zones=[],
              detector={"kind": "gstreamer", "model": "yolov8s"},
              camera={"source": "0", "width": 320, "height": 240, "fps": 10}))


def drive3(r):
    wait(lambda: r.counts().get("fn_kick_runs", 0) >= 1)
    check("6 gst kind, AI off: no pipeline, no camera",
          GST["builds"] == 0 and CAM["opens"] == 0, (GST, CAM["opens"]))
    r.set(ai={"on": True})
    on = wait(lambda: r.state().get("ai_ok") is True)
    check("6 AI on: the pipeline is built and the dot turns green",
          on and GST["live"] == 1, (GST, r.state().get("ai_error")))
    check("6 ...and no pipe opens a camera beside it (one owner)",
          not CAM["live"], CAM)
    r.set(ai={"on": False})
    off = wait(lambda: r.state().get("ai_on") is False and GST["live"] == 0)
    time.sleep(0.6)
    st_ = r.state()
    check("6 AI off: the pipeline is released, no camera opened in its place",
          off and not CAM["live"], (GST, CAM))
    check("6 ...and the state carries no camera fault",
          not st_.get("error") and not st_.get("ai_error"), st_)
    check("6 ...the dot's reason is 'AI detection is off'",
          dc._ai_reason({"running": True, "mode": "unified", "ai_on": False},
                        True, False) == "AI detection is off")
    r.set(ai={"on": True})
    check("6 on again: rebuilt", wait(lambda: r.state().get("ai_ok") is True
                                      and GST["live"] == 1), GST)
    r.set(ai={"on": False})
    wait(lambda: GST["live"] == 0)


check("the gstreamer app ran", r3.go(drive3) == 0)
check("6 every pipeline built was released", GST["live"] == 0
      and GST["builds"] == 2, GST)

# -- 5: a removed system stays down --------------------------------------------------
from core.services import provision                            # noqa: E402
order = []
saved = (dc.set_powered_off, dc.stop_app, provision.stop_master,
         provision.config.systems, provision.config.save_local)
dc.set_powered_off = lambda sd, slug, off: order.append(("power_off", off))
dc.stop_app = lambda sd, slug: order.append(("stop_app",))
provision.stop_master = lambda url: order.append(("stop_master",)) or False
provision.config.systems = lambda *a, **k: {"enabled": ["demo", "other"],
                                            "masters": {"demo": {"url": "http://x"}}}
provision.config.save_local = lambda *a, **k: None
try:
    provision.remove_system("/nowhere", "demo", ["demo", "other"],
                            tempfile.mkdtemp())
except Exception as exc:                                       # noqa: BLE001
    order.append(("raised", repr(exc)))
finally:
    (dc.set_powered_off, dc.stop_app, provision.stop_master,
     provision.config.systems, provision.config.save_local) = saved
check("5 remove powers the system off BEFORE stopping it",
      order[:3] == [("power_off", True), ("stop_app",), ("stop_master",)],
      order)
gen = tempfile.mkdtemp()
check("5 the master asks the same question at start",
      "provision_service.removed_reason(slug, systems_cfg, generated_dir)"
      in read("master_server.py"))
check("5 a master refuses a built-in system the Hub no longer enables",
      "not enabled" in provision.removed_reason(
          "demo", {"enabled": ["other"]}, gen))
check("5 ...but runs one it does, and any when there is no list",
      provision.removed_reason("demo", {"enabled": ["demo"]}, gen) == ""
      and provision.removed_reason("demo", {}, gen) == "")

# A refused master stands down instead of crash-looping under systemd. The
# module needs uvicorn, which a dev box may not have: the function is lifted
# out of the source and run with stubs.
import ast                                                     # noqa: E402
tree = ast.parse(ms_src)
picked = [n for n in tree.body
          if (isinstance(n, ast.FunctionDef) and n.name == "stand_down")
          or (isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "REMOVED_EXIT" for t in n.targets))]
ns = {"os": os, "time": time,
      "logger": type("L", (), {"error": staticmethod(lambda *a, **k: None)})()}
exec(compile(ast.Module(body=picked, type_ignores=[]), "master_server", "exec"), ns)
os.environ["IRIV_REMOVED_EXIT"] = "78"
try:
    ns["stand_down"]("removed")
    code = None
except SystemExit as exc:
    code = exc.code
finally:
    os.environ.pop("IRIV_REMOVED_EXIT", None)
check("5' a unit that knows the status: the master exits 78 and stays down",
      code == 78 and ns["REMOVED_EXIT"] == 78, code)
naps = []


def nap(s_):
    naps.append(s_)
    if len(naps) >= 3:
        raise KeyboardInterrupt


try:
    ns["stand_down"]("removed", sleep=nap)
except KeyboardInterrupt:
    pass
check("5' an older unit: it idles (no exit, so no 5 s restart loop)",
      len(naps) == 3 and all(n >= 60 for n in naps), naps)
main_src = ms_src[ms_src.index("def main():"):]
check("5' ...decided before anything binds the port",
      main_src.index("removed_reason(") < main_src.index("uvicorn.run("))
unit = read("scripts", "install.sh")
check("5' the master unit lets a removed system's exit stand",
      "RestartPreventExitStatus=78" in unit
      and "Environment=IRIV_REMOVED_EXIT=78" in unit)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all AI-review checks passed")
