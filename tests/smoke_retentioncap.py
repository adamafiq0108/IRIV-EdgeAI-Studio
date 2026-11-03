# Retention maximums follow where the data IS (Tok Aba 2026-10-05).
#
# With the data on an NVMe, every row of the Data Retention page may be set
# up to 12 months (the three day rows up to 365 days). Without it -- the data
# on the built-in SD/eMMC, even with an NVMe fitted and not yet holding the
# store -- Event archives, Snapshots and Video clips stop at 60 days; the two
# months rows keep their 12. The cap is enforced by the service, not only the
# input, and a value saved above it is KEPT as saved, applied at the cap,
# and said once (smoke_nvmeunmount.py covers the drive coming back).
# Pinned with fake devices (retention._dev_of / _device_info / _nvme_fitted):
#   1. no systems yet, store on the eMMC: 60 days / 12 months, and the words
#      say longer retention needs an NVMe
#   2. the store on an NVMe: 365 days (12 months); a box that BOOTS from its
#      NVMe counts too
#   3. an NVMe fitted but the store still on the eMMC: 60, and the words say
#      the data is not on the NVMe drive yet
#   4. the service refuses above the cap in plain words (no HTTP codes), for
#      the day rows and the months rows, and a refusal writes nothing
#   5. a saved 365 on a box without its data on the NVMe: read as 60, the
#      saved 365 left on disk, said ONCE, and the start date is left alone
#   6. the sweep honours the cap: a stored 365 on the eMMC deletes a 70-day
#      clip, keeps a 50-day one
#   7. the route hands the systems to set_policy and answers with the notice;
#      the page shows the max beside every field and refuses above it
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE

from core import config as config_mod                          # noqa: E402
from core.services import retention as ret                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
# Device numbers: 1 = the root disk, 2 = an NVMe. Where the store "is" and
# what the root disk is are set per test.
WHERE = {"store": 1}
ROOTDISK = {"name": "mmcblk0", "removable": False, "usb": False}
NVME = {"name": "nvme0n1", "removable": False, "usb": False}
FITTED = {"nvme": False}


def fake_dev(path):
    p = os.path.abspath(str(path))
    if p.startswith(os.path.abspath(STORE)) or p.startswith(os.path.abspath(PKG_ROOT)):
        return WHERE["store"]
    return 1


PKG_ROOT = tempfile.mkdtemp()
ret._dev_of = fake_dev
ret._device_info = lambda dev, sys_root="/sys": {1: ROOTDISK, 2: NVME}.get(dev)
ret._nvme_fitted = lambda sys_root="/sys": FITTED["nvme"]
ret.remember_pkg_dirs([])


def refusal(updates):
    try:
        ret.set_policy(updates, CFG)
        return ""
    except ValueError as e:
        return str(e)


def stored():
    return dict(config_mod.load_local("storage", CFG).get("retention") or {})


# -- 1. the eMMC, no systems yet ------------------------------------------------------
lim = ret.limits(CFG)
check("1 store on the eMMC: the three day rows stop at 60",
      all(lim[k] == 60 for k in ret.MEDIA_KEYS), lim)
check("1 ...the months rows keep their 12",
      lim["history_months"] == 12 and lim["events_months"] == 12, lim)
reason = ret.storage_tier(CFG)["reason"]
check("1 ...and the words say longer retention needs an NVMe",
      "on the built-in storage: up to 60 days" in reason
      and "Longer retention needs an NVMe drive" in reason, reason)

# -- 2. the store on an NVMe; a box booting from its NVMe ------------------------------
WHERE["store"] = 2
lim = ret.limits(CFG)
check("2 store on the NVMe: every day row up to 365 (12 months)",
      all(lim[k] == 365 for k in ret.MEDIA_KEYS)
      and "on the NVMe: up to 12 months" in ret.storage_tier(CFG)["reason"],
      (lim, ret.storage_tier(CFG)))
WHERE["store"] = 1
ROOTDISK.update(name="nvme0n1")
check("2 ...a box that boots from its NVMe keeps its data there: 365",
      ret.limits(CFG)["clips_days"] == 365, ret.storage_tier(CFG))
ROOTDISK.update(name="mmcblk0")

# -- 3. an NVMe fitted, the store not moved ----------------------------------------------
FITTED["nvme"] = True
reason = ret.storage_tier(CFG)["reason"]
check("3 an NVMe fitted but the store on the eMMC: still 60",
      ret.limits(CFG)["snapshots_days"] == 60, ret.limits(CFG))
check("3 ...and the words say the data is not on the NVMe drive yet",
      "An NVMe drive is fitted, but the data is not on it" in reason
      and "needs the data on the NVMe drive" in reason, reason)

# -- 4. the service refuses above the cap ---------------------------------------------------
ret.set_policy({"clips_days": 30}, CFG)
before = stored()
msg = refusal({"snapshots_days": 61, "clips_days": 40})
check("4 61 days on a box without its data on the NVMe is refused, in words",
      msg == ("Snapshots can be kept for up to 60 days on this box: its data "
              "is not on the NVMe drive. Longer retention needs the data on "
              "the NVMe drive."), msg)
check("4 ...no HTTP code and no key name in the wording",
      "HTTP" not in msg and "40" not in msg and "_days" not in msg, msg)
check("4 ...and nothing was written (clips stays 30, not 40)",
      stored() == before and ret.policy(CFG)["clips_days"] == 30, stored())
check("4 60 itself is accepted",
      refusal({"snapshots_days": 60}) == ""
      and ret.policy(CFG)["snapshots_days"] == 60)
msg = refusal({"history_months": 13})
check("4 the months rows refuse 13 in words",
      msg == "Analytics history can be kept for up to 12 months.", msg)
WHERE["store"] = 2
msg = refusal({"events_days": 366})
check("4 on the NVMe 366 is refused as past 12 months",
      msg == "Event archives can be kept for up to 12 months (365 days).", msg)
check("4 ...and 365 is accepted", refusal({"events_days": 365}) == "")

# -- 5. a stored value above the cap ------------------------------------------------------
set_at = stored().get("set_at")
WHERE["store"] = 1                          # the data is back on the eMMC
check("5 a stored 365 reads as 60 where the box allows 60",
      ret.policy(CFG)["events_days"] == 60
      and stored()["events_days"] == 365, (ret.policy(CFG), stored()))
v = ret.view(CFG)
check("5 the first load says so, once, in plain words",
      v["notice"] == ["Event archives: kept for 60 days while the data is on "
                      "the built-in storage. The saved 12 months applies "
                      "again when the data is back on the NVMe drive."],
      v["notice"])
check("5 ...and keeps the saved 365, leaving the start date alone",
      stored()["events_days"] == 365 and stored().get("set_at") == set_at
      and v["saved"]["events_days"] == 365, stored())
check("5 the next load says nothing", ret.view(CFG)["notice"] == [])
check("5 the page's field shows what applies, 60",
      v["policy"]["events_days"] == 60 and v["limits"]["events_days"] == 60, v)

# -- 6. the sweep honours the cap -----------------------------------------------------------
pkg = os.path.join(PKG_ROOT, "alpha")
data = os.path.join(PKG_ROOT, "data")
clips = os.path.join(data, "clips")
os.makedirs(pkg)
os.makedirs(clips)
ret.dc._merged = lambda pkg_dir: {}
ret.dc._data_dir = lambda pkg_dir, cfg: data
local = config_mod.load_local("storage", CFG)
local["retention"] = dict(local.get("retention") or {}, clips_days=365)
config_mod.save_local("storage", local, CFG)   # a hand edit, never loaded
now = time.time()
for name, days in (("old", 70), ("young", 50)):
    path = os.path.join(clips, f"{name}.mp4")
    open(path, "wb").write(b"x")
    os.utime(path, (now - days * 86400, now - days * 86400))
ret.set_starts_at(int(now - 400 * 86400), CFG)
report = ret.sweep([pkg], CFG, now=now)
check("6 a stored 365 on the eMMC: the sweep deletes the 70-day clip",
      not os.path.exists(os.path.join(clips, "old.mp4")), report)
check("6 ...and keeps the 50-day one",
      os.path.exists(os.path.join(clips, "young.mp4")), report)
check("6 ...reporting 60 as what applied",
      report.get("effective", {}).get("alpha", {}).get("clips_days") == 60,
      report.get("effective"))

# -- 7. the route and the page ----------------------------------------------------------------
hub = io.open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8").read()
check("7 the save route hands the systems to set_policy",
      "retention_service.set_policy(body, st.config_dir, pkg_dirs)" in hub)
panel = io.open(os.path.join(ROOT, "frontend", "src", "components",
                             "RetentionPanel.jsx"), encoding="utf-8").read()
check("7 the page shows the max beside every field and refuses above it",
      "data-retention-max={f.key}" in panel and "maxWords(f.unit, max)" in panel
      and "disabled={busy || anyOver}" in panel
      and "More than this box allows" in panel, "")
check("7 ...and shows the one-time notice",
      panel.count('toast.info("Kept for 60 days for now", n)') == 2)
src = io.open(os.path.join(ROOT, "core", "services", "retention.py"),
              encoding="utf-8").read()
check("7 no em dash in what this change touches",
      all(chr(0x2014) not in s for s in (src, panel, hub)))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all retention-cap checks passed")
