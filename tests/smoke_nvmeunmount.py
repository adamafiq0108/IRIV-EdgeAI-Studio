# -*- coding: utf-8 -*-
# smoke_nvmeunmount.py -- an NVMe the box expects and does not have mounted
# (Tok Aba 2026-10-05).
#
# Only the NVMe unlocks 12 months. When it is not mounted the box saves on the
# built-in storage and keeps that data 60 days, raises an alarm, and NEVER
# writes into the empty mount point on the eMMC. Checks:
#   1. a saved 365 survives the unmount: 60 applies (page + sweep), the saved
#      value is unchanged on disk, and the saved 365 applies again on remount
#   2. the "kept for 60 days" sentence is said once per spell, by a flag, not
#      by rewriting the value -- and said again for the next spell
#   3. the alarm: raised once (Inbox notice to the admins) while the drive is
#      missing, cleared once (a second notice) when it is back; the banner
#      words are the agreed ones; a box that never had an NVMe never alarms;
#      fstab listing the NVMe counts as expecting it
#   4. no write into an unmounted mount point: CYTRON_STORE, the app's
#      [app].data_dir, an explicit [storage].archive_dir and an explicit
#      CYTRON_NVME all fall back to the built-in storage, and the mount point
#      stays empty
#   5. what the app saved on the eMMC meanwhile is swept there at 60 days once
#      the data is back on the NVMe; the NVMe's own data keeps its 365
#   6. the shell, the Settings page and the hub wire the banner up; no em dash
#   7. the way out (Yaya's QA part 1, bug 8): storage_watch.forget drops the
#      seen-on-this-drive memory, the alarm goes with its own Inbox notice
#      (not "mounted again"), and what still expects the drive (fstab, a
#      configured path) is named instead of the banner coming back unexplained;
#      the route is admin-only and the page has the button
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE
os.environ.pop("CYTRON_NVME", None)

from core import config as config_mod                          # noqa: E402
from core.services import retention as ret                     # noqa: E402
from core.services import storage_watch as sw                  # noqa: E402
from app import config as app_config                           # noqa: E402
from app import store as app_store                             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


TMP = tempfile.mkdtemp()
NVME = os.path.join(TMP, "mnt", "nvme")
os.makedirs(NVME)                                  # the mount point, always there
FSTAB = os.path.join(TMP, "fstab")
open(FSTAB, "w", encoding="utf-8").write("UUID=1 / ext4 defaults 0 1\n")
config_mod.NVME_MOUNT = NVME
config_mod.FSTAB = FSTAB
MOUNTED = {"on": True}
_real_ismount = os.path.ismount
os.path.ismount = lambda p: (MOUNTED["on"] and os.path.normpath(str(p))
                             == os.path.normpath(NVME)) or (
    os.path.normpath(str(p)) != os.path.normpath(NVME) and _real_ismount(p))

# One system whose data follows the app's rule: on the NVMe while mounted,
# its own ./data on the eMMC otherwise.
PKG = os.path.join(TMP, "store", "systems", "alpha")
os.makedirs(PKG)
NV_DATA = os.path.join(NVME, "cytron", "alpha", "data")
LOCAL = os.path.join(PKG, "data")
ret.dc._merged = lambda pkg_dir: {}
ret.dc._data_dir = lambda pkg_dir, cfg: NV_DATA if MOUNTED["on"] else LOCAL


def fake_dev(path):
    p = os.path.abspath(str(path))
    return 2 if MOUNTED["on"] and p.startswith(os.path.abspath(NVME)) else 1


ret._dev_of = fake_dev
ret._device_info = lambda dev, sys_root="/sys": {
    1: {"name": "mmcblk0", "removable": False, "usb": False},
    2: {"name": "nvme0n1", "removable": False, "usb": False}}.get(dev)
ret._nvme_fitted = lambda sys_root="/sys": True

CFG = tempfile.mkdtemp()
SENT = []


def notify(kind, title, body="", config_dir=None, page="", tab="", **_):
    SENT.append((kind, title, body, page, tab))


def watch(cfg=CFG, pkgs=None):
    return sw.check([PKG] if pkgs is None else pkgs, cfg, notify=notify)


def stored():
    return dict(config_mod.load_local("storage", CFG).get("retention") or {})


ret.remember_pkg_dirs([PKG])

# -- 1. a saved 365 survives the unmount ------------------------------------------------
ret.set_policy({"events_days": 365, "clips_days": 365}, CFG, [PKG])
check("1 mounted: 365 saved and applied",
      ret.policy(CFG)["clips_days"] == 365 and stored()["clips_days"] == 365,
      (ret.policy(CFG), stored()))
w = watch()
check("1 ...no alarm while the drive is mounted, and the drive is remembered",
      not w["active"] and not SENT and os.path.normpath(NVME) in [
          os.path.normpath(m) for m in config_mod.load_local("storage", CFG)
          .get("storage_watch", {}).get("mounts", [])], (w, SENT))

MOUNTED["on"] = False
v = ret.view(CFG, [PKG])
check("1 unmounted: 60 applies on the page",
      v["policy"]["events_days"] == 60 and v["policy"]["clips_days"] == 60, v["policy"])
check("1 ...the saved 365 is still on disk and reported as saved",
      stored()["events_days"] == 365 and stored()["clips_days"] == 365
      and v["saved"]["events_days"] == 365, (stored(), v.get("saved")))
before = stored()
try:
    ret.set_policy({"snapshots_days": 90}, CFG)
    msg = ""
except ValueError as e:
    msg = str(e)
check("1 ...a value above 60 typed now is still refused, in words, writing nothing",
      "up to 60 days" in msg and stored() == before, (msg, stored()))

# -- 2. the notice, once per spell -------------------------------------------------------
check("2 the first load says so once, naming both rows and the saved period",
      v["notice"] == ["Event archives and Video clips: kept for 60 days while "
                      "the data is on the built-in storage. The saved 12 months "
                      "applies again when the data is back on the NVMe drive."],
      v["notice"])
check("2 ...by a flag, the values untouched",
      stored().get(ret.NOTICE_FLAG) is True and stored()["clips_days"] == 365,
      stored())
check("2 the next load says nothing", ret.view(CFG, [PKG])["notice"] == [])

# -- 3. the alarm ------------------------------------------------------------------------
w = watch()
check("3 unmounted: the alarm is raised with the agreed words",
      w["active"] and w["text"] == (
          "The NVMe drive is not mounted. New data is being saved on the "
          "built-in storage and kept for 60 days. Mount the drive again (or "
          "restart the box) to go back to 12 months."), w)
check("3 ...one Inbox notice to the admins, pointing at Data Retention",
      len(SENT) == 1 and SENT[0][1] == sw.ALARM_TITLE
      and SENT[0][3:] == ("settings", "retention"), SENT)
watch()
check("3 ...the next check does not post it again", len(SENT) == 1, SENT)
check("3 ...the page carries it for the banner",
      ret.view(CFG, [PKG])["alarm"]["active"] is True)

# -- 5 (first half). the sweep while unmounted: 60 on the eMMC data ---------------------
now = time.time()
os.makedirs(os.path.join(LOCAL, "clips"), exist_ok=True)
for name, days in (("emmc_old", 70), ("emmc_young", 50)):
    path = os.path.join(LOCAL, "clips", f"{name}.mp4")
    open(path, "wb").write(b"x")
    os.utime(path, (now - days * 86400, now - days * 86400))
ret.set_starts_at(int(now - 400 * 86400), CFG)
report = ret.sweep([PKG], CFG, now=now)
check("5 unmounted: the sweep applies 60 to the eMMC data",
      not os.path.exists(os.path.join(LOCAL, "clips", "emmc_old.mp4"))
      and os.path.exists(os.path.join(LOCAL, "clips", "emmc_young.mp4"))
      and report["effective"]["alpha"]["clips_days"] == 60, report)
check("5 ...and the saved 365 is still saved after the sweep",
      stored()["clips_days"] == 365, stored())

# -- 1/3 again: the drive comes back -----------------------------------------------------
MOUNTED["on"] = True
v = ret.view(CFG, [PKG])
check("1 remounted: the saved 365 applies again",
      v["policy"]["events_days"] == 365 and v["policy"]["clips_days"] == 365,
      v["policy"])
check("2 ...nothing over any more: no sentence, and the flag is cleared",
      v["notice"] == [] and ret.NOTICE_FLAG not in stored(), stored())
w = watch()
check("3 remounted: the alarm clears, with one notice saying what happens "
      "to the data saved meanwhile",
      not w["active"] and len(SENT) == 2 and SENT[1][1] == sw.CLEAR_TITLE
      and "not moved or merged" in SENT[1][2] and "60 days" in SENT[1][2], (w, SENT))
watch()
check("3 ...and stays cleared without another notice", len(SENT) == 2, SENT)

# -- 5. the eMMC leftovers, with the data back on the NVMe -------------------------------
os.makedirs(os.path.join(NV_DATA, "clips"), exist_ok=True)
for where, name, days in ((LOCAL, "left_old", 70), (LOCAL, "left_young", 50),
                          (NV_DATA, "nvme_old", 70)):
    path = os.path.join(where, "clips", f"{name}.mp4")
    open(path, "wb").write(b"x")
    os.utime(path, (now - days * 86400, now - days * 86400))
report = ret.sweep([PKG], CFG, now=now)
check("5 remounted: what was saved on the eMMC meanwhile goes at 60 days",
      not os.path.exists(os.path.join(LOCAL, "clips", "left_old.mp4"))
      and os.path.exists(os.path.join(LOCAL, "clips", "left_young.mp4")), report)
check("5 ...while the NVMe's 70-day clip keeps its 365",
      os.path.exists(os.path.join(NV_DATA, "clips", "nvme_old.mp4"))
      and report["effective"]["alpha"]["clips_days"] == 365, report)

# -- 2 again: the next spell is said again -----------------------------------------------
MOUNTED["on"] = False
check("2 a second unmount says it again, once",
      len(ret.view(CFG, [PKG])["notice"]) == 1
      and ret.view(CFG, [PKG])["notice"] == [])
MOUNTED["on"] = True
ret.view(CFG, [PKG])

# -- 3. which boxes alarm at all ---------------------------------------------------------
CFG2 = tempfile.mkdtemp()
MOUNTED["on"] = False
SENT.clear()
check("3 a box that never had our data on an NVMe, and no fstab line: no alarm",
      not watch(CFG2, [])["active"] and SENT == [], SENT)
open(FSTAB, "w", encoding="utf-8").write(
    f"UUID=1 / ext4 defaults 0 1\nUUID=dead {NVME} ext4 defaults,nofail 0 2\n")
check("3 fstab lists the NVMe and it is not mounted: alarm",
      watch(CFG2, [])["active"] and len(SENT) == 1, SENT)
open(FSTAB, "w", encoding="utf-8").write("UUID=1 / ext4 defaults 0 1\n")

# -- 4. nothing is written into the unmounted mount point --------------------------------
def empty_mount():
    return os.listdir(NVME) in ([], ["cytron"]) and not os.path.exists(
        os.path.join(NVME, "cytron", "store")) and not os.path.exists(
        os.path.join(NVME, "elsewhere"))


import shutil                                                    # noqa: E402
shutil.rmtree(os.path.join(NVME, "cytron"), ignore_errors=True)
HOME = os.path.join(TMP, "home", "store")
saved_cands = config_mod.STORE_CANDIDATES
config_mod.STORE_CANDIDATES = [os.path.join(NVME, "cytron", "store"), HOME]
os.environ["CYTRON_STORE"] = os.path.join(NVME, "cytron", "store")
config_mod._store_cache.clear()
err, sys.stderr = sys.stderr, io.StringIO()
try:
    chosen = config_mod.store_dir()
    said = sys.stderr.getvalue()
finally:
    sys.stderr = err
check("4 CYTRON_STORE on the unmounted NVMe: the built-in store is used",
      os.path.normpath(str(chosen)) == os.path.normpath(HOME), (chosen, said))
check("4 ...the mount point stays empty, and the log line says why",
      os.listdir(NVME) == [] and "NOT mounted" in said
      and "nothing is written into the empty mount point" in said,
      (os.listdir(NVME), said))
config_mod.STORE_CANDIDATES = saved_cands
os.environ["CYTRON_STORE"] = STORE
config_mod._store_cache.clear()

app_config.FSTAB = FSTAB
saved_root = app_config.NVME_ROOT
app_config.NVME_ROOT = NVME             # the app's own idea of the NVMe
# ...listed in fstab as install.sh writes it: a root that is neither
# /mnt/nvme nor an fstab mount is an ordinary folder, taken at its word.
open(FSTAB, "w", encoding="utf-8").write(
    f"UUID=1 / ext4 defaults 0 1\nUUID=2 {NVME} ext4 defaults 0 2\n")
app_pkg = os.path.join(TMP, "pkg_beta")
os.makedirs(app_pkg)
target = os.path.join(NVME, "elsewhere", "data")
got = app_config.data_dir({"app": {"slug": "beta", "data_dir": target}}, app_pkg)
check("4 the app's [app].data_dir on the unmounted NVMe: ./data on the "
      "built-in storage", os.path.normpath(got) == os.path.normpath(
          os.path.join(app_pkg, "data")) and os.listdir(NVME) == [], got)
MOUNTED["on"] = True
got = app_config.data_dir({"app": {"slug": "beta", "data_dir": target}}, app_pkg)
check("4 ...and the configured dir again once the drive is mounted",
      os.path.normpath(got) == os.path.normpath(target), got)
shutil.rmtree(os.path.join(NVME, "elsewhere"), ignore_errors=True)
MOUNTED["on"] = False

data = os.path.join(TMP, "store_data", "data")
os.makedirs(data)
st = app_store.Store(data, archive_dir=os.path.join(NVME, "archive"))
check("4 an explicit archive dir on the unmounted NVMe: archived beside the data",
      os.path.normpath(st.archive_dir) == os.path.normpath(
          os.path.join(TMP, "store_data", "archive")), st.archive_dir)

other = os.path.join(TMP, "mnt", "ssd")
os.makedirs(other)
open(FSTAB, "w", encoding="utf-8").write(f"UUID=2 {other} ext4 defaults 0 2\n")
app_config.NVME_ROOT = other
check("4 an explicit CYTRON_NVME that fstab lists and is not mounted: unusable",
      app_config._nvme_usable() is None and os.listdir(other) == [])
app_config.NVME_ROOT = saved_root
check("4 core's guard names the same mount point",
      config_mod.unmounted_mount_of(os.path.join(other, "x")) is not None
      and config_mod.unmounted_mount_of(os.path.join(TMP, "home")) is None)
# An explicit CYTRON_NVME that fstab does NOT list is an ordinary folder the
# operator chose (a dev box, the smoke tests): taken at its word by the app
# and by core alike. Guarding it unconditionally (a48b238f) answered "no
# second storage" for every such box and broke smoke_nvmeprobe on Linux.
plain = os.path.join(TMP, "mnt", "plain")
os.makedirs(plain)
app_config.NVME_ROOT = plain
check("4 an explicit CYTRON_NVME that fstab does not list is taken at its word",
      app_config._nvme_usable() == plain
      and config_mod.unmounted_mount_of(os.path.join(plain, "x")) is None
      and os.listdir(plain) == [],
      (app_config._nvme_usable(), config_mod.unmounted_mount_of(plain)))
app_config.NVME_ROOT = saved_root
# ...but a mount storage_watch has seen our data on is a drive to core's
# guard even with no fstab line, the same "expected" rule as the alarm.
import pathlib                                                   # noqa: E402
seen_mount = os.path.join(TMP, "mnt", "seen")
os.makedirs(seen_mount)
CFG2 = tempfile.mkdtemp()
sw._save_state({"mounts": [seen_mount]}, CFG2)
# The memory is read from the config dir the caller names (as every other
# load_local does), not from the process default: storage_watch.check(cfg)
# and the guard agree on the same file.
check("4 a mount remembered by storage_watch is guarded without an fstab line",
      config_mod.unmounted_mount_of(os.path.join(seen_mount, "x"), config_dir=CFG2)
      == os.path.normpath(seen_mount)
      and config_mod.unmounted_mount_of(os.path.join(plain, "x"), config_dir=CFG2) is None,
      config_mod.guarded_mounts(CFG2))
saved_cfg_dir = config_mod._CONFIG_DIR
config_mod._CONFIG_DIR = pathlib.Path(CFG2)
try:
    check("4 ...and with no config dir named, the process default is read",
          config_mod.unmounted_mount_of(os.path.join(seen_mount, "x"))
          == os.path.normpath(seen_mount))
finally:
    config_mod._CONFIG_DIR = saved_cfg_dir
check("4 another config dir does not know that mount",
      config_mod.unmounted_mount_of(os.path.join(seen_mount, "x"),
                                    config_dir=tempfile.mkdtemp()) is None)
check("4 at the end, nothing was ever created in the unmounted NVMe mount point",
      os.listdir(NVME) == [], os.listdir(NVME))

# -- 7. forgetting the drive --------------------------------------------------------------
CFG3 = tempfile.mkdtemp()
MOUNTED["on"] = False
SENT.clear()
sw._save_state({"mounts": [NVME]}, CFG3)         # our data was seen on it once
check("7 a remembered drive, unmounted: the alarm is on",
      watch(CFG3, [])["active"] and len(SENT) == 1 and SENT[0][1] == sw.ALARM_TITLE, SENT)
SENT.clear()
out = sw.forget(CFG3, [], notify=notify)
check("7 forget(): the alarm goes, the memory is empty, the mount is named as forgotten",
      out["alarm"]["active"] is False and out["forgotten"] == [os.path.normpath(NVME)]
      and out["still_expected"] == {}
      and sw._state(CFG3).get("mounts") == [] and sw._state(CFG3).get("alarm") is False, out)
check("7 ...one Inbox notice in its own words, NOT 'mounted again'",
      len(SENT) == 1 and SENT[0][1] == sw.FORGOT_TITLE and "60 days" in SENT[0][2]
      and SENT[0][3:] == ("settings", "retention"), SENT)
check("7 ...the next check stays quiet", not watch(CFG3, [])["active"] and len(SENT) == 1)
SENT.clear()
out = sw.forget(CFG3, [], notify=notify)
check("7 nothing remembered: forget() is a no-op, no notice",
      out["forgotten"] == [] and SENT == [] and not out["alarm"]["active"], out)
# fstab still lists the drive: forgetting the memory cannot un-expect it, and
# the answer says which line keeps it expected instead of a banner that just
# comes back.
open(FSTAB, "w", encoding="utf-8").write(
    f"UUID=1 / ext4 defaults 0 1\nUUID=dead {NVME} ext4 defaults,nofail 0 2\n")
sw._save_state({"mounts": [NVME], "alarm": True, "missing": [NVME]}, CFG3)
SENT.clear()
out = sw.forget(CFG3, [], notify=notify)
check("7 fstab still names the drive: the alarm stays and the reason is fstab",
      out["alarm"]["active"] and out["still_expected"] == {
          os.path.normpath(NVME): f"listed in {config_mod.FSTAB}"}
      and SENT == [], (out, SENT))
open(FSTAB, "w", encoding="utf-8").write("UUID=1 / ext4 defaults 0 1\n")
# a setting naming the drive keeps it expected too
os.environ["CYTRON_STORE"] = os.path.join(NVME, "cytron", "store")
config_mod._store_cache.clear()
SENT.clear()
err, sys.stderr = sys.stderr, io.StringIO()
try:
    out = sw.forget(CFG3, [], notify=notify)
finally:
    sys.stderr = err
check("7 a setting still names the drive: the alarm stays and says so",
      out["alarm"]["active"] and "named by a setting" in out["still_expected"].get(
          os.path.normpath(NVME), ""), out)
os.environ["CYTRON_STORE"] = STORE
config_mod._store_cache.clear()
sw.forget(CFG3, [], notify=notify)
check("7 ...and once nothing names it, forget() clears it",
      not watch(CFG3, [])["active"])
check("7 the mount point was never written into", os.listdir(NVME) == [], os.listdir(NVME))

# -- 6. wiring --------------------------------------------------------------------------
def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


hub = read("core", "routes", "hub.py")
shell = read("frontend", "src", "components", "HubShell.jsx")
panel = read("frontend", "src", "components", "RetentionPanel.jsx")
server = read("hub_server.py")
check("6 the hub poll carries the alarm and the shell draws the banner",
      '"storage_alarm": storage_watch.current(cfg)' in hub
      and "data?.storage_alarm?.active" in shell
      and "data.storage_alarm.text" in shell)
check("6 Settings shows the banner and the saved period",
      "alarm?.active" in panel and "data-retention-saved" in panel)
route = read("core", "routes", "storage.py")
check("6 the forget route is admin-only, mounted, and the page has the button",
      "is_site_admin" in route and "storage_watch.forget" in route
      and "app.include_router(storage_router)" in server
      and '"/api/hub/retention/nvme/forget"' in panel and "nvme-forget" in panel)
check("6 the hub checks every minute, not only at start",
      "storage_watch.check(" in server and "CHECK_EVERY_S" in server)
srcs = [read("core", "services", "storage_watch.py"), route,
        read("core", "services", "retention.py"), read("core", "config.py"),
        read("systems", "demo", "app", "config.py"),
        read("systems", "demo", "app", "store.py"), hub, shell, panel, server]
check("6 no em dash in what this change touches",
      all(chr(0x2014) not in s for s in srcs))

os.path.ismount = _real_ismount
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all nvme-unmount checks passed")
