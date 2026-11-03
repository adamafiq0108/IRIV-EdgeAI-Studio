# -*- coding: utf-8 -*-
# After an OTA the hub brings back the systems the update took down
# (core/services/update_revive.py). Boards .21/.22, 2026-10-05: the applier
# re-spawned every running master from inside irivai-apply.service, a oneshot
# whose cgroup systemd sweeps when it exits, so each master died seconds
# after verify_masters() had called it back; the post-flip hub had skipped
# autostart ("update in progress") and nothing started them later.
#
# Pinned here, with the store in a temp dir and every box call injected:
#   1. a finished job with was_masters [demo, uat_test] and demo already
#      answering: uat_test is spawned exactly once, demo never; marker written
#   2. a system that never answers: ATTEMPTS spawns with the backoff between,
#      then one Inbox notice to the admins naming it in plain words
#   3. nothing to do: no status file, an idle phase, a job already revived
#      for, a job from another boot -- start() returns None, no spawn
#   4. an older applier that recorded nothing: the masters whose log the
#      doomed spawn wrote come back, an Autostart system comes back, a system
#      with neither is left alone; a record that names no master means none
#      was running, and the logs are not consulted
#   5. a master with its own systemd unit is left to systemd; a powered-off
#      system is left down; neither gets the notice
#   6. start() end to end: waits for job_running() to drop, then revives once
#   7. the sources: the hub hands over to update_revive after its autostart
#      loop and no longer "skips" for ever; running_masters() writes
#      was_masters.json; install.sh's apply unit has KillMode=process;
#      upgrade_steps.sh step 11 adds it idempotently and never mid-update
#
#   py -3 tests/smoke_updaterevive.py
import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp(prefix="smoke_revive_store_")

from core.services import update as upd                        # noqa: E402
from core.services import update_revive as ur                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


BASE = tempfile.mkdtemp(prefix="smoke_revive_base_")
CFG = tempfile.mkdtemp(prefix="smoke_revive_cfg_")
MASTERS = {"demo": {"url": "http://127.0.0.1:5110", "autostart": False},
           "uat_test": {"url": "http://127.0.0.1:5102", "autostart": False},
           "remote": {"url": "http://10.0.0.9:5102"}}
upd._boot_id = lambda: "boot-A"
ur._unit_active = lambda: False
ur._SLEEP = lambda s: None
ur.GRACE_S = 0


def _write(name, data):
    with open(upd._p(name), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _rm(name):
    try:
        os.remove(upd._p(name))
    except OSError:
        pass


def job(started, phase="done", boot="boot-A", record=True, masters=("demo", "uat_test")):
    _rm("revived.json")
    _write("status.json", {"phase": phase, "percent": 100, "started": started,
                           "version": "1.0.16", "pid": 0, "boot_id": boot})
    if record:
        _write("was_masters.json", {"started": started,
                                    "masters": {s: 5110 for s in masters}})
    else:
        _rm("was_masters.json")


class Box:
    def __init__(self, alive=(), comes_up=(), powered_off=()):
        self.alive = set(alive)
        self.comes_up = set(comes_up)
        self.powered_off = set(powered_off)
        self.spawned, self.slept, self.notices = [], [], []

    def answers(self, url):
        return any(u["url"] == url for s, u in MASTERS.items() if s in self.alive)

    def bring_up(self, slug, entry):
        if slug in self.powered_off:
            return {"master": "skipped", "app": "skipped",
                    "message": "powered off by the operator - left down"}
        self.spawned.append(slug)
        if slug in self.comes_up:
            self.alive.add(slug)
        return {"master": "started", "app": "started",
                "message": "master: started; app: started"}

    def notify(self, kind, title, body, config_dir, **kw):
        self.notices.append((kind, title, body, kw))

    def run(self, **kw):
        kw.setdefault("owned", set())
        return ur.revive(MASTERS, "/sys", BASE, CFG, ur.pending(),
                         answers=self.answers, bring_up=self.bring_up,
                         sleep=self.slept.append, notify=self.notify, **kw)


# -- 1. the missing one, exactly once -----------------------------------------
job(1000)
box = Box(alive={"demo"}, comes_up={"uat_test"})
rep = box.run()
check("1. uat_test spawned exactly once, demo never",
      box.spawned == ["uat_test"], box.spawned)
check("   report: revived uat_test, demo already up, nothing given up",
      rep == {"revived": ["uat_test"], "already": ["demo"], "gave_up": [], "left": {}}, rep)
check("   no notice", box.notices == [], box.notices)
check("   one backoff wait, then the look that found it answering",
      box.slept == [ur.BACKOFF_S[0]], box.slept)
mark = json.load(open(upd._p("revived.json"), encoding="utf-8"))
check("   marker names the job and what came back",
      mark.get("started") == 1000 and mark.get("revived") == ["uat_test"], mark)
check("   ...and pending() is now None: a later hub restart does nothing",
      ur.pending() is None)
check("   the remote master (another box) was never considered",
      "remote" not in box.spawned)

# -- 2. never answers: bounded, then the Inbox ---------------------------------
job(2000)
box = Box(alive={"demo"})
rep = box.run()
check("2. gave up after ATTEMPTS spawns", box.spawned == ["uat_test"] * ur.ATTEMPTS, box.spawned)
check("   backoff between every try", box.slept == list(ur.BACKOFF_S[:ur.ATTEMPTS]), box.slept)
check("   report says so", rep["gave_up"] == ["uat_test"] and rep["revived"] == [], rep)
check("   one notice to the admins, kind update, pointing at the Hub page",
      len(box.notices) == 1 and box.notices[0][0] == "update"
      and box.notices[0][3].get("page") == "systems", box.notices)
title, body = (box.notices[0][1], box.notices[0][2]) if box.notices else ("", "")
check("   ...in plain words: names the system and its port, says what to do",
      "1 system did not come back after the update" == title
      and "uat_test (port 5102)" in body and "press Start" in body
      and "v1.0.16" in body, (title, body))
check("   ...no em dash anywhere in it", chr(0x2014) not in title + body)
mark = json.load(open(upd._p("revived.json"), encoding="utf-8"))
check("   marker records the give-up", mark.get("gave_up") == ["uat_test"], mark)

# -- 3. nothing to do -------------------------------------------------------------
_rm("status.json"); _rm("was_masters.json"); _rm("revived.json")
check("3. no status file: start() is None", ur.start(MASTERS, "/sys", BASE, CFG) is None)
_write("status.json", {"phase": "idle"})
check("   idle: pending() None and start() None",
      ur.pending() is None and ur.start(MASTERS, "/sys", BASE, CFG) is None)
job(3000)
check("   a finished job IS pending", ur.pending() == {"started": 3000, "phase": "done", "version": "1.0.16"})
_write("revived.json", {"started": 3000})
check("   ...unless already revived for", ur.pending() is None)
job(3001, boot="boot-OLD")
check("   a job from an earlier boot: None (that boot's autostart did it)", ur.pending() is None)
job(3002, phase="gate")
check("   a job still in its gate is not pending yet", ur.pending() is None)
job(3003, phase="rolled_back")
check("   a rolled-back job is pending (its systems were stopped too)",
      (ur.pending() or {}).get("phase") == "rolled_back")
job(3004, phase="refused")
check("   a refused job is not (nothing was stopped)", ur.pending() is None)

# -- 4. the older applier recorded nothing ----------------------------------------
job(5000, record=False)
os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
log = os.path.join(BASE, "logs", "master-uat_test.log")
open(log, "a").close()
os.utime(log, (5010, 5010))
want = ur.wanted(MASTERS, 5000, BASE)
check("4. no record: the master whose log the doomed spawn wrote is wanted",
      want == {"uat_test": 5102}, want)
os.utime(log, (4000, 4000))
check("   ...a log from before the job is not", ur.wanted(MASTERS, 5000, BASE) == {})
m2 = {k: dict(v) for k, v in MASTERS.items()}
m2["demo"]["autostart"] = True
check("   an Autostart system is wanted with or without a record",
      ur.wanted(m2, 5000, BASE) == {"demo": 5110})
job(5001, masters=("uat_test",))
check("   with a record, the record decides (plus Autostart)",
      ur.wanted(m2, 5001, BASE) == {"demo": 5110, "uat_test": 5102})
check("   a record from another job is ignored", ur.wanted(MASTERS, 5002, BASE) == {})
# An empty record is a record: the applier looked and saw no master running,
# so nothing is restarted from the logs (which would bring back a system the
# operator had stopped before the update); Autostart ones still come back.
job(5003, masters=())
log = os.path.join(BASE, "logs", "master-uat_test.log")
open(log, "a").close()
os.utime(log, (5010, 5010))
check("   an empty record means nothing was running: the logs are not consulted",
      ur.wanted(MASTERS, 5003, BASE) == {}, ur.wanted(MASTERS, 5003, BASE))
check("   ...but an Autostart system still comes back",
      ur.wanted(m2, 5003, BASE) == {"demo": 5110}, ur.wanted(m2, 5003, BASE))

# -- 5. left alone: systemd's and the powered off ---------------------------------
job(6000)
box = Box(alive=set())
rep = box.run(owned={"demo"})
check("5. a unit-managed master is left to systemd, not spawned",
      "demo" not in box.spawned and rep["left"].get("demo") == "its own systemd unit restarts it", rep)
check("   ...it is not in the notice either",
      box.notices and "demo" not in box.notices[0][2], box.notices)
job(6001)
box = Box(alive={"demo"}, powered_off={"uat_test"})
rep = box.run()
check("   a powered-off system stays down, once asked, no notice",
      box.spawned == [] and rep["left"].get("uat_test") == "powered off by the operator"
      and box.notices == [] and box.slept == [], (rep, box.notices))

# -- 6. start(): waits for the applier, then one pass -------------------------------
job(7000)
box = Box(alive={"demo"}, comes_up={"uat_test"})
ticks = {"n": 3}
upd.job_running = lambda: ticks.__setitem__("n", ticks["n"] - 1) or ticks["n"] > 0
from core.services import provision as prov                    # noqa: E402
prov._master_answers = box.answers
prov.autostart_system = lambda sysdir, slug, entry, base: box.bring_up(slug, entry)
t = ur.start(MASTERS, "/sys", BASE, CFG)
check("6. start() returns its thread while a job is running", t is not None)
if t:
    t.join(20)
check("   the thread waited the applier out, then spawned uat_test once",
      ticks["n"] <= 0 and box.spawned == ["uat_test"], (ticks, box.spawned))
check("   and marked the job", ur.pending() is None)
check("   a second start() for the same job does nothing", ur.start(MASTERS, "/sys", BASE, CFG) is None)

# -- 7. the sources ---------------------------------------------------------------
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("7. the hub hands over to update_revive after its autostart loop",
      "update_revive.start(app.state.masters" in hub
      and hub.index("autostart_system(systems_dir, slug") < hub.index("update_revive.start("))
check("   ...and no longer skips autostart for ever", "skipping autostart" not in hub)
au = open(os.path.join(ROOT, "scripts", "apply_update.py"), encoding="utf-8").read()
src = au.split("def running_masters(job):", 1)[1].split("\ndef ", 1)[0]
check("   running_masters() records was_masters.json for the hub",
      '"was_masters.json"' in src and '"started": job.started' in src)
inst = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
unit = inst.split('irivai-apply.service "[Unit]', 1)[1].split("write_unit", 1)[0]
check("   install.sh: the apply unit has KillMode=process", "\nKillMode=process\n" in unit)
up = open(os.path.join(ROOT, "scripts", "upgrade_steps.sh"), encoding="utf-8").read()
step = up.split('say "11. apply unit', 1)
check("   upgrade_steps.sh has step 11, before the hub restart",
      len(step) == 2 and up.index('say "11. apply unit') < up.index("if [[ $RESTART_HUB -eq 1 ]]; then"))
body = step[1] if len(step) == 2 else ""
check("   ...idempotent: skips when already set, never mid-update, reloads systemd",
      "grep -qE '^KillMode=process$'" in body and "is-active irivai-apply.service" in body
      and "systemctl daemon-reload" in body and 'KillMode=process' in body)
check("   ...the help range covers the new header line",
      re.search(r"sed -n '2,(\d+)p'", up) and up.splitlines()[int(re.search(r"sed -n '2,(\d+)p'", up).group(1)) - 1].startswith("# script):"))
mod = open(os.path.join(ROOT, "core", "services", "update_revive.py"), encoding="utf-8").read()
check("   no em dash in the module, the test or the shell step",
      chr(0x2014) not in mod + body + open(__file__, encoding="utf-8").read())

# -- the applier's record, for real, against a fake job ------------------------------
import importlib.util                                           # noqa: E402
spec = importlib.util.spec_from_file_location("_au", os.path.join(ROOT, "scripts", "apply_update.py"))
aum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aum)
work = tempfile.mkdtemp(prefix="smoke_revive_work_")


class _J:
    work = work
    started = 8000
    config_dir = CFG


aum.masters_config = lambda job: {"a": 5111, "b": 5112}
aum.health = lambda port, timeout=3: {"ok": True} if port == 5111 else None
got = aum.running_masters(_J())
rec = json.load(open(os.path.join(work, "was_masters.json"), encoding="utf-8"))
check("   running_masters() still returns the answering ones and writes them down",
      got == {"a": 5111} and rec == {"started": 8000, "masters": {"a": 5111}}, (got, rec))

print()
print("all good" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
