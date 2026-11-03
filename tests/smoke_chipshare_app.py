# The Hailo load balancer, end to end without a chip: the box switch, the
# app's live read of it, the heartbeat files, the safe fallback, the
# read-only route and the Settings > System card.
#
#   a. [system] chip_balance: off by default, saved through sysconfig, and
#      read by the APP through the directory core exports (IRIVAI_BOX_CONFIG)
#      -- live, a flip seen on the next read without a restart
#   b. a running Balancer follows the switch tick by tick (on -> caps,
#      off -> None), and its heartbeat holds ids and numbers, never a URL
#   c. fail safe: a corrupt fresh heartbeat, a share dir that is not a
#      directory, or our own write missing -> None (today's fixed rule)
#   d. core's snapshot: per-camera rows with a share of the chip in use,
#      whitelisted fields only, unknown reasons read as "off"
#   e. the route is admin-only and read-only, mounted on hub and masters;
#      the switch saves through POST /api/system/settings and is audited
#   f. run.py reads the switch on the 5 s tick and times both infer paths
#   g. the card: "(testing)", the one-line hint, a 5 s poll of the route,
#      a label for every reason
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
sys.path.insert(0, ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


from app import chipshare as cs                                     # noqa: E402
from core.services import chip_balance as core_cb                   # noqa: E402
from core.services import sysconfig                                 # noqa: E402

CFG = os.path.join(tempfile.mkdtemp(), "config")
shutil.copytree(os.path.join(ROOT, "config"), CFG)

# -- a. the switch ---------------------------------------------------------------------
check("the balancer is off unless the box says otherwise",
      sysconfig.chip_balance(CFG) is False and sysconfig.settings(CFG)["chip_balance"] is False)
check("the committed system.toml documents the key, off",
      "chip_balance = false" in read("config", "system.toml"))
check("an app with no exported directory reads it as off", cs.switch_on({}) is False)
sysconfig.export_location(CFG)
env = {cs.CONFIG_ENV: os.environ[sysconfig.BOX_CONFIG_ENV]}
check("core exports the config directory the app reads",
      os.path.samefile(env[cs.CONFIG_ENV], CFG))
check("...where the app reads it as off", cs.switch_on(env) is False)
check("turned on through sysconfig", sysconfig.set_chip_balance(True, CFG) is True)
check("...the app sees it on the next read, no restart", cs.switch_on(env) is True)
time.sleep(0.05)
sysconfig.set_chip_balance(False, CFG)
local = os.path.join(CFG, "system.local.toml")
os.utime(local, (time.time() + 2, time.time() + 2))   # a coarse mtime still moves
check("...and off again on the next read", cs.switch_on(env) is False)
with open(local, "w", encoding="utf-8") as f:
    f.write('[system]\nchip_balance = "true"\n')
os.utime(local, (time.time() + 4, time.time() + 4))
check('"true" typed into the file is on, like core reads it', cs.switch_on(env) is True
      and sysconfig.chip_balance(CFG) is True)
with open(local, "w", encoding="utf-8") as f:
    f.write("[system\nchip_balance = tru")
os.utime(local, (time.time() + 6, time.time() + 6))
check("a file that does not parse reads as off (the committed default)",
      cs.switch_on(env) is False)

# -- b. a running Balancer follows the switch ------------------------------------------
# Ticks a second apart, not five: the files' ages are read from the real
# clock (mtime), so the test's clock must stay within STALE_S of it.
SHARE = os.path.join(tempfile.mkdtemp(), "share")
now = time.time()
b = cs.Balancer(pid=4242, now=now - 600, share_dir=SHARE)
cams = [("main", 30.0, 29.0), ("cam2", 25.0, 24.0)]
first = b.tick(now, True, cams, slug="people", model="yolov8s", cost=1.0)
check("on: the balancer answers per camera (a free chip caps nobody)",
      first == {"main": None, "cam2": None}, first)
check("off on the next tick: None, so run.py takes the table again",
      b.tick(now + 1, False, cams, slug="people", model="yolov8s") is None
      and set(b.reasons.values()) == {"off"})
check("on again: per-camera answers again",
      isinstance(b.tick(now + 2, True, cams, slug="people", model="yolov8s"), dict))
path = os.path.join(SHARE, "4242.json")
beat_text = open(path, encoding="utf-8").read()
beat = json.loads(beat_text)
check("the heartbeat is this app's own file", beat["pid"] == 4242 and beat["slug"] == "people")
check("...with ids and numbers only: no URL, no source, no credential",
      "://" not in beat_text and "source" not in beat_text and "rtsp" not in beat_text
      and sorted(beat["cams"][0]) == ["demand", "fps", "id", "reason", "target"])
check("...and only a path's last part for the model",
      cs.Balancer(pid=1).entry(now, "s", "/secret/dir/yolov8m.hef", 3.0, True, True)["model"]
      == "yolov8m.hef")
if os.name != "nt":
    mode = stat.S_IMODE(os.stat(path).st_mode)
    check("the file is 0644 and the dir 0755 (others read, only we write)",
          mode == 0o644 and stat.S_IMODE(os.stat(SHARE).st_mode) & 0o022 == 0, oct(mode))

# -- c. fail safe ----------------------------------------------------------------------
with open(os.path.join(SHARE, "999.json"), "w", encoding="utf-8") as f:
    f.write("{not json")
check("a corrupt FRESH heartbeat: fall back to the fixed rule",
      b.tick(now + 3, True, cams, slug="people") is None
      and set(b.reasons.values()) == {"fallback"})
os.utime(os.path.join(SHARE, "999.json"), (now - 60, now - 60))
check("...and once it is stale it is ignored and balancing resumes",
      isinstance(b.tick(now + 4, True, cams, slug="people"), dict))
os.utime(os.path.join(SHARE, "999.json"), (now - 400, now - 400))
b.tick(now + 5, True, cams, slug="people")
check("...and a long-dead file is tidied away",
      not os.path.exists(os.path.join(SHARE, "999.json")))
not_dir = os.path.join(tempfile.mkdtemp(), "share")
open(not_dir, "w").close()
c = cs.Balancer(pid=4343, now=now - 600, share_dir=not_dir)
check("a share 'dir' that is a file: nothing written, fixed rule",
      c.tick(now, True, cams) is None and c.mode == "fallback")
check("a snapshot without our own entry is not trusted",
      cs.plan(cs.read_beats(now + 5, SHARE), now + 5, me=1) is None)
check("a missing share dir reads as no snapshot",
      cs.read_beats(now, os.path.join(SHARE, "nope")) is None)

# -- d. core's snapshot ----------------------------------------------------------------
SNAP = tempfile.mkdtemp()
for pid, slug, model, cost, fps, reason in (
        (1, "people", "yolov8s", 1.0, 20.0, "fair"),
        (2, "vehicles", "yolov8m", 3.0, 10.0, "floor")):
    with open(os.path.join(SNAP, f"{pid}.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "ts": now, "slug": slug, "model": model, "cost": cost,
                   "chip": True, "mode": "saturated", "capacity": 50.0,
                   "source": "rtsp://user:pw@cam/stream1",
                   "cams": [{"id": "main", "fps": fps, "target": fps, "demand": 30,
                             "reason": reason, "url": "rtsp://user:pw@cam"}]}, f)
with open(os.path.join(SNAP, "3.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 3, "ts": now, "slug": "odd", "chip": True,
               "cams": [{"id": "x", "fps": 5, "reason": "<script>"}]}, f)
snap = core_cb.snapshot(now=now, path=SNAP)
rows = {r["slug"]: r for r in snap["rows"]}
check("a row per camera, sorted, with the systems counted",
      [r["slug"] for r in snap["rows"]] == ["odd", "people", "vehicles"]
      and snap["systems"] == 3, snap)
check("share = this camera's fps x cost over the box's",
      rows["people"]["share"] == round(100 * 20 / 55, 1)
      and rows["vehicles"]["share"] == round(100 * 30 / 55, 1), rows)
check("nothing outside the whitelist reaches the page",
      "rtsp" not in json.dumps(snap) and "url" not in rows["people"]
      and "source" not in snap)
check("an unknown reason reads as off, never as text from a file",
      rows["odd"]["reason"] == "off")
check("an empty or missing share dir is an empty table",
      core_cb.snapshot(now=now, path=os.path.join(SNAP, "none"))["rows"] == [])

# -- e. the route ----------------------------------------------------------------------
route = read("core", "routes", "chip_balance.py")
check("GET /api/system/chip-balance is master or developer only",
      '@router.get("/api/system/chip-balance"' in route
      and "if not auth_service.is_admin(me, cfg):" in route
      and "master or developer only" in route)
check("...and read-only: no write verb in the file",
      not any(f"@router.{v}" in route for v in ("post", "put", "patch", "delete")))
for server in ("hub_server.py", "master_server.py"):
    src = read(server)
    check(f"{server} mounts it and exports the config directory",
          "app.include_router(chip_balance_router)" in src
          and "_sysconfig.export_location(" in src)
auth_src = read("core", "routes", "auth.py")
post = auth_src.split('name="system.settings_save"', 1)[1].split("@router.", 1)[0]
check("the switch saves through POST /api/system/settings, only when named, audited",
      'if "chip_balance" in body:' in post
      and "sysconfig_service.set_chip_balance(" in post
      and "hailo load balancer" in post)

# -- f. run.py -------------------------------------------------------------------------
run_src = read("systems", "demo", "app", "run.py")
tick = run_src.split("if now2 >= next_balance:", 1)[1].split("interval = 1.0 /", 1)[0]
check("run.py reads the switch live on the balance tick",
      "chipshare_mod.switch_on()" in tick and "chip_bal.tick(" in tick)
check("...applies a per-camera cap, the table's when there is none",
      "cap_ = cap if fair is None else fair.get(pipe.id, cap)" in tick
      and "chip_bal.note_applied(applied)" in tick)
check("...and a balancer error can only mean the fixed rule",
      "except Exception:                           # noqa: BLE001\n                    fair = None" in tick)
check("both infer paths report their wall time",
      run_src.count("chip_bal.note_infer(time.monotonic() - t0") == 2)
check("the app still imports nothing from core",
      not re.search(r"^\s*(from|import)\s+core",
                    read("systems", "demo", "app", "chipshare.py"), re.M))

# -- g. the card -----------------------------------------------------------------------
panel = read("frontend", "src", "components", "SystemPanel.jsx")
card = read("frontend", "src", "components", "ChipBalanceCard.jsx")
check("Settings > System carries the card, saving through the box switches",
      "<ChipBalanceCard" in panel and "save({ chip_balance: on })" in panel
      and "data.chip_balance" in panel)
check('the card is marked "(testing)" and says what it does in one line',
      "Hailo load balancer (testing)" in card
      and "Splits the AI chip's frame rate fairly between all cameras on this" in card
      and "Off = today's fixed rule." in card)
check("the table polls the read-only route every 5 s, only while mounted and visible",
      'usePoll("/api/system/chip-balance", 5000)' in card)
check("...shown when on, or when more than one system shares the chip",
      "on || (data?.systems || 0) > 1" in card)
check("every reason the app can give has words on the card",
      all(f"  {r}:" in card for r in cs.REASONS))
check("the columns asked for", all(h in card for h in (
    "Model", "Target fps", "Measured fps", "Share", "Why")))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
