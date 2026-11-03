# The Hailo load balancer's allocator (systems/demo/app/chipshare.py).
#
# Owner's ask (2026-10-11): an optional, box-wide balancer that splits the AI
# chip's frame rate fairly between every camera on the box, stably, with
# today's fixed rule when it is off. Pinned here without a chip:
#   a. the duplicated constants agree (floor, share dir, staleness, env name)
#   b. the cost table: prefix match, unknown = 1.0, a per-system override
#   c. water_fill: weighted max-min in chip time, floors, demand release,
#      never above demand, 1 / 4 / 8 cameras, mixed models
#   d. plan: deterministic in any order, stale heartbeats gone, fail safe on
#      a missing / malformed snapshot or one without our own entry, busy-time
#      saturation with hysteresis, the start-up hold
#   e. smooth: dead band, bounded step up, fast down only when saturated,
#      the floor, a released cap climbing back and then dropped
#   f. OFF (or no chip) answers None, so run.py keeps today's cap
#   g. a simulated chip for 300 ticks: converges, no oscillation, never under
#      the floor, fair between equal models, cheap models get more frames,
#      an app joining mid-run settles after its hold
import os
import random
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
sys.path.insert(0, ROOT)
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


from app import chipshare as cs                                     # noqa: E402
from core.services import chip_balance as core_cb                   # noqa: E402
from core.services import sysconfig                                 # noqa: E402

# -- a. duplicated on purpose, pinned equal -------------------------------------------
run_src = read("systems", "demo", "app", "run.py")
dc_src = read("core", "services", "detector_control.py")
run_min = float(re.search(r"^MIN_CAMERA_FPS = ([\d.]+)", run_src, re.M).group(1))
dc_min = float(re.search(r"^MIN_CAMERA_FPS = ([\d.]+)", dc_src, re.M).group(1))
check("the balancer's floor is the app's and core's MIN_CAMERA_FPS",
      cs.MIN_FPS == run_min == dc_min, (cs.MIN_FPS, run_min, dc_min))
check("core reads the same share dir with the same staleness",
      core_cb.SHARE_DIRNAME == cs.SHARE_DIRNAME and core_cb.STALE_S == cs.STALE_S)
check("core exports the env name the app reads",
      sysconfig.BOX_CONFIG_ENV == cs.CONFIG_ENV)
check("core knows every reason the app gives", set(core_cb.REASONS) == set(cs.REASONS))

# -- b. the cost table ----------------------------------------------------------------
check("yolov8m costs three yolov8s", cs.model_cost("yolov8m") == 3.0
      and cs.model_cost("yolov8s") == 1.0)
check("matched by prefix on the file name",
      cs.model_cost("yolov8m_coco.hef") == 3.0
      and cs.model_cost("/x/models/hailo8/yolov8n.hef") == 0.5)
check("an unknown model costs the default", cs.model_cost("my_model") == cs.DEFAULT_COST)
check("[detector] chip_cost wins", cs.model_cost("yolov8n", 2.5) == 2.5)
check("...unless it is not a positive number",
      cs.model_cost("yolov8n", "x") == 0.5 and cs.model_cost("yolov8n", -1) == 0.5)

# -- c. water_fill ---------------------------------------------------------------------
wf = cs.water_fill([("a", 1.0, 30)], 20)
check("one camera, chip short: capped at the capacity", abs(wf["a"][0] - 20) < 1e-9
      and wf["a"][1] == "fair", wf)
wf = cs.water_fill([(k, 1.0, 30) for k in "abcd"], 80)
check("four equal cameras split evenly", all(abs(v[0] - 20) < 1e-9 for v in wf.values()), wf)
wf = cs.water_fill([(k, 1.0, 30) for k in "abcdefgh"], 120)
check("eight equal cameras: 15 each", all(abs(v[0] - 15) < 1e-9 for v in wf.values()), wf)
wf = cs.water_fill([(k, 1.0, 30) for k in "abcdefgh"], 40)
check("eight cameras on a small chip: nobody under the floor",
      all(v == (10.0, "floor") for v in wf.values()), wf)
wf = cs.water_fill([("n", 0.5, 100), ("m", 3.0, 100)], 70)
check("same chip time, cheap model more frames (n 6x m)",
      abs(wf["n"][0] * 0.5 - wf["m"][0] * 3.0) < 1e-6
      and abs(wf["n"][0] / wf["m"][0] - 6) < 1e-6, wf)
wf = cs.water_fill([("slow", 1.0, 5), ("a", 1.0, 30), ("b", 1.0, 30)], 45)
check("a camera that cannot use its share releases it to the others",
      wf["slow"] == (5.0, "demand") and abs(wf["a"][0] - 20) < 1e-9
      and abs(wf["b"][0] - 20) < 1e-9, wf)
wf = cs.water_fill([("a", 1.0, 12), ("b", 1.0, 30)], 1000)
check("never above what a camera delivers", wf["a"] == (12.0, "demand")
      and wf["b"] == (30.0, "demand"), wf)
wf = cs.water_fill([("s1", 1.0, 30), ("s2", 1.0, 30), ("m", 3.0, 25),
                    ("n1", 0.5, 30), ("n2", 0.5, 30), ("n3", 0.5, 30)], 120)
check("mixed: n at its demand, m held at the floor, s takes the rest",
      wf["n1"] == (30.0, "demand") and wf["m"] == (10.0, "floor")
      and abs(wf["s1"][0] - 22.5) < 1e-9, wf)
three = [("s1", 1.0, 30), ("s2", 1.0, 30), ("m", 3.0, 25)]
wf = cs.water_fill(three, 70)
used = sum(wf[k][0] * c for k, c, _d in three)
check("the shares use exactly the capacity when the floors do not bind",
      abs(used - 70) < 1e-6, used)

# -- d. plan ---------------------------------------------------------------------------
NOW = 1_000_000.0


def beat(pid, cams, cost=1.0, busy=0.2, mode="free", capped=False, peak=0.0,
         ts=NOW, started=NOW - 600, chip=True):
    return {"pid": pid, "ts": ts, "started": started, "chip": chip, "cost": cost,
            "busy": busy, "mode": mode, "capped": capped, "peak": peak,
            "cams": [{"id": c, "demand": d, "fps": f} for c, d, f in cams]}


snap = [beat(1, [("a", 30, 25), ("b", 30, 25)], busy=0.5),
        beat(2, [("a", 25, 10)], cost=3.0, busy=0.5),
        beat(3, [("a", 30, 28)], cost=0.5, busy=0.1)]
p1 = cs.plan(snap, NOW, me=1)
shuffled = list(snap)
random.Random(4).shuffle(shuffled)
check("the same snapshot gives the same plan in any order and in any app",
      p1 == cs.plan(shuffled, NOW, me=2) == cs.plan(list(reversed(snap)), NOW, me=3))
check("busy summed past BUSY_ENTER is saturated, capacity = what was served",
      p1["mode"] == "saturated" and abs(p1["capacity"] - (25 + 25 + 30 + 14)) < 1e-6, p1)
check("...and the shares are weighted max-min of it",
      p1["targets"][(3, "a")] == (30.0, "demand")
      and p1["targets"][(2, "a")][1] in ("floor", "fair"), p1["targets"])
stale = snap + [beat(9, [("x", 30, 30)], busy=5.0, ts=NOW - cs.STALE_S - 1)]
check("a heartbeat older than STALE_S is gone (its busy does not count)",
      cs.plan(stale, NOW, me=1) == p1)
check("no snapshot: fall back", cs.plan(None, NOW, me=1) is None)
check("our own heartbeat missing: fall back", cs.plan(snap, NOW, me=77) is None)
check("a fresh entry that is not a heartbeat: fall back",
      cs.plan(snap + [{"pid": "x", "ts": NOW}], NOW, me=1) is None
      and cs.plan(snap + ["junk"], NOW, me=1) is None
      and cs.plan(snap + [beat(5, [("a", -3, 1)])], NOW, me=1) is None)
quiet = [beat(1, [("a", 30, 30)], busy=0.3), beat(2, [("a", 30, 30)], busy=0.3)]
pq = cs.plan(quiet, NOW, me=1)
check("a chip with idle time caps nobody",
      pq["mode"] == "free" and pq["capacity"] is None
      and all(v == (None, "free") for v in pq["targets"].values()), pq)
warm = [beat(1, [("a", 30, 30)], busy=0.4, mode="saturated"),
        beat(2, [("a", 30, 30)], busy=0.4)]
check("hysteresis: busy 0.8 stays saturated if it already was ...",
      cs.plan(warm, NOW, me=1)["mode"] == "saturated")
warm[0]["mode"] = "free"
check("... and does not enter it", cs.plan(warm, NOW, me=1)["mode"] == "free")
warm[0]["mode"] = "saturated"
warm[0]["busy"] = warm[1]["busy"] = 0.3
check("... and leaves below BUSY_EXIT", cs.plan(warm, NOW, me=1)["mode"] == "free")
probe = [beat(1, [("a", 30, 20)], busy=0.3, capped=True, peak=40),
         beat(2, [("a", 30, 20)], busy=0.3)]
pp = cs.plan(probe, NOW, me=1)
check("caps left on an idle chip are probed upward, not kept",
      pp["mode"] == "probe" and abs(pp["capacity"] - 40 * (1 + cs.PROBE)) < 1e-6, pp)
held = snap + [beat(4, [("a", 30, 0)], started=NOW - 5)]
check("an app that started under HOLD_S ago holds the box",
      cs.plan(held, NOW, me=1)["hold"] is True and p1["hold"] is False)
check("an app that is not on the chip is not counted",
      cs.plan(snap + [beat(6, [("a", 30, 30)], busy=9.0, chip=False)], NOW, me=1) == p1)
solo = cs.plan([beat(1, [("a", 30, 14), ("b", 30, 14)], busy=1.0)], NOW, me=1)
check("one app alone on the chip (Hailo-10H exclusive): its own cameras share it",
      solo["mode"] == "saturated"
      and solo["targets"][(1, "a")] == solo["targets"][(1, "b")]
      and abs(solo["targets"][(1, "a")][0] - 14) < 1e-6, solo)

# -- e. smooth -------------------------------------------------------------------------
check("inside the dead band nothing moves", cs.smooth(20.0, 22.5, 30, True) == 20.0
      and cs.smooth(30.0, 26.0, 30, True) == 30.0)
check("up is at most STEP_UP a tick", cs.smooth(12.0, 30.0, 30, True) == 14.0)
check("down goes straight to the target when saturated",
      cs.smooth(30.0, 12.0, 30, True) == 12.0)
check("...and STEP_DOWN at a time when it is not", cs.smooth(30.0, 12.0, 30, False) == 28.0)
check("never under the floor", cs.smooth(15.0, 3.0, 30, True) == cs.MIN_FPS)
check("a first cap starts from the camera's own rate",
      cs.smooth(None, 20.0, 30, False) == 28.0 and cs.smooth(None, 20.0, 30, True) == 20.0)
check("no cap stays no cap", cs.smooth(None, None, 30, False) is None)
seq, cap = [], 20.0
for _ in range(10):
    cap = cs.smooth(cap, None, 30, False)
    seq.append(cap)
    if cap is None:
        break
check("a released cap climbs back STEP_UP at a time, then is dropped",
      seq[:3] == [22.0, 24.0, 26.0] and seq[-1] is None, seq)

# -- f. off is today's rule ------------------------------------------------------------
D = tempfile.mkdtemp()
b = cs.Balancer(pid=11, now=NOW - 600, share_dir=os.path.join(D, "s"))
check("switch off: tick answers None (run.py keeps the _fps_share cap)",
      b.tick(NOW, False, [("a", 30, 30)]) is None and b.reasons == {"a": "off"})
check("switch on but not on the chip: None too",
      b.tick(NOW + 5, True, [("a", 30, 30)], chip=False) is None)
check("run.py uses the table's cap whenever the balancer answers None",
      "cap_ = cap if fair is None else fair.get(pipe.id, cap)" in run_src)


# -- g. a simulated chip ---------------------------------------------------------------
def simulate(spec, X, ticks=300, join=None, seed=1):
    """`spec` = [(model, [camera delivery fps])], one app each. The chip
    serves X cost-units/s; when asked for more, the runtime's scheduler
    splits it per PROCESS (as hailort_service does), which is the unfairness
    the balancer is there to fix. Busy time = asked / X (everybody waits
    once it is over 1). Measurements carry +-3% noise."""
    rng = random.Random(seed)
    clock = [NOW]
    cs._mtime = lambda _p: clock[0]
    share = tempfile.mkdtemp()
    apps = []

    def add(pid, model, delivs):
        apps.append({"pid": pid, "cost": cs.model_cost(model), "model": model,
                     "cams": [(f"c{i}", d) for i, d in enumerate(delivs)],
                     "bal": cs.Balancer(pid=pid, now=clock[0], share_dir=share),
                     "targets": {}})
    for i, (model, delivs) in enumerate(spec):
        add(100 + i, model, delivs)
    hist = {}
    for t in range(ticks):
        clock[0] += 5.0
        if join and t == join[0]:
            add(200, join[1], join[2])
        asks = []
        for a in apps:
            tg = [min(a["targets"].get(c, max(cs.MIN_FPS, d)), d) for c, d in a["cams"]]
            a["rate"] = min(tg)              # one loop, paced by its slowest camera
            asks.append(a["rate"] * len(a["cams"]) * a["cost"])
        got, left, todo = list(asks), X, list(range(len(asks)))
        if sum(asks) > X:
            while todo:
                each = left / len(todo)
                small = [i for i in todo if asks[i] <= each]
                if not small:
                    for i in todo:
                        got[i] = each
                    break
                for i in small:
                    left -= asks[i]
                    todo.remove(i)
        for a, ask, g in zip(apps, asks, got):
            served = g / (len(a["cams"]) * a["cost"]) * rng.uniform(0.97, 1.03)
            busy = min(1.0, ask / X) * rng.uniform(0.95, 1.05)
            a["bal"].note_infer(busy * 5.0, max(1, int(served * 5)))
            caps = a["bal"].tick(clock[0], True,
                                 [(c, d, served) for c, d in a["cams"]],
                                 slug=f"s{a['pid']}", model=a["model"], cost=a["cost"])
            applied = {}
            for c, d in a["cams"]:
                cap_ = caps.get(c) if caps is not None else None
                applied[c] = (min(max(cs.MIN_FPS, cap_), d) if cap_ is not None
                              else max(cs.MIN_FPS, d))
                hist.setdefault((a["pid"], c), []).append(applied[c])
            a["targets"] = applied
            a["bal"].note_applied(applied)
    load = sum(min(a["targets"][c], d) * a["cost"] for a in apps for c, d in a["cams"])
    cs._mtime = os.path.getmtime
    return hist, load


def flips(seq):
    n, last = 0, 0
    for x, y in zip(seq, seq[1:]):
        d = (y > x) - (y < x)
        if d and last and d != last:
            n += 1
        if d:
            last = d
    return n


cases = [
    ("1 camera, chip short", [("yolov8m", [30])], 40),
    ("2 systems, room to spare", [("yolov8s", [30, 30]), ("yolov8s", [25])], 300),
    ("4 cameras, 2 systems, m beside n", [("yolov8m", [25, 25]), ("yolov8n", [30, 30])], 120),
    ("8 cameras, 3 systems on s", [("yolov8s", [30] * 4), ("yolov8s", [30] * 2),
                                   ("yolov8s", [30] * 2)], 160),
    ("6 cameras, mixed s / m / n", [("yolov8s", [30, 30]), ("yolov8m", [25]),
                                    ("yolov8n", [30, 30, 30])], 120),
    ("8 m cameras, floors exceed the chip", [("yolov8m", [30] * 4),
                                             ("yolov8m", [30] * 4)], 150),
]
for name, spec, X in cases:
    hist, load = simulate(spec, X)
    tail = {k: v[60:] for k, v in hist.items()}
    worst = max(flips(v) for v in tail.values())
    floor_ok = all(min(v) >= cs.MIN_FPS for v in hist.values())
    settled = all(max(v[-50:]) - min(v[-50:]) <= cs.HYST_FPS for v in hist.values())
    check(f"sim {name}: settles, <= 2 direction changes per camera, never under 10",
          worst <= 2 and floor_ok and settled,
          {k: (round(v[-1], 1), flips(v[60:])) for k, v in hist.items()})
    floors = sum(cs.MIN_FPS * cs.model_cost(m) for m, ds in spec for _ in ds)
    check(f"sim {name}: the chip is not asked for more than it has (or only the floors)",
          load <= max(X * 1.05, floors + 1e-6), (round(load, 1), X))

hist, _l = simulate([("yolov8s", [30, 30]), ("yolov8m", [25]), ("yolov8n", [30, 30, 30])], 120)
check("sim: the mixed box ends where water_fill says (s ~22.5, m 10, n 30)",
      abs(hist[(100, "c0")][-1] - 22.5) <= 2.5 and hist[(101, "c0")][-1] == 10
      and hist[(102, "c0")][-1] == 30, {k: v[-1] for k, v in hist.items()})
hist, _l = simulate([("yolov8s", [30] * 4), ("yolov8s", [30] * 2), ("yolov8s", [30] * 2)], 160)
ends = [v[-1] for v in hist.values()]
check("sim: equal models end within 15% of each other whatever the process",
      max(ends) <= min(ends) * 1.15, ends)
hist, _l = simulate([("yolov8m", [30, 30]), ("yolov8n", [30, 30])], 90)
check("sim: the cheap model ends with more frames than the expensive one",
      hist[(101, "c0")][-1] > hist[(100, "c0")][-1],
      (hist[(100, "c0")][-1], hist[(101, "c0")][-1]))
hist, load = simulate([("yolov8s", [30, 30]), ("yolov8s", [30])], 100,
                      join=(100, "yolov8s", [30, 30]))
before = [hist[(100, "c0")][95], hist[(101, "c0")][95]]
during = [hist[(100, "c0")][101 + i] for i in range(4)]
check("sim: an app joining mid-run holds every cap for HOLD_S ...",
      len(set(during)) == 1 and during[0] == hist[(100, "c0")][100], during)
tail = {k: v[-100:] for k, v in hist.items()}
check("... then the box settles on the new split without oscillating",
      max(flips(v) for v in tail.values()) <= 2 and load <= 100 * 1.05,
      {k: round(v[-1], 1) for k, v in hist.items()})

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
