# Device-data retention follows the drive each system's data is on (Tok Aba
# 5i, with Yaya's review of 69ab27e and the lead's decisions; caps moved to
# 60 / 12 months and NVMe-only by Tok Aba 2026-10-05, smoke_retentioncap).
#
# events_days / snapshots_days / clips_days are capped per system: up to 365
# for a system whose data and archive dirs are all on an NVMe (not removable,
# not on USB), 60 otherwise (the built-in eMMC, a USB stick, a fixed drive
# that is not an NVMe, a drive that cannot be identified, a system whose
# config cannot be read). history_months / events_months stay 12.
# Pinned with fake devices (retention._dev_of / _device_info):
#   1. one system on the eMMC: 60 applies; a 365 is refused in words, and the
#      reason says longer retention needs an NVMe
#   2. the same system on a fixed NVMe: 365 applies, said as "the NVMe"
#   3. a USB drive (removable, or on usb) keeps 60 and says why; a SATA/other
#      fixed drive is not the NVMe either: 60
#   4. two systems, one on each: each keeps its own cap, the page is told they
#      differ, and the sweep deletes a 70-day clip on the eMMC system but not
#      on the NVMe one, and nothing younger than 60 days anywhere
#   5. a system whose config cannot be read counts as eMMC (60), not skipped
#   6. a Save of another value while 365 is allowed leaves the stored 365
#      alone (the page sends only what changed)
#   7. months stay 12; the route and the page carry chosen vs applied
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config as config_mod                          # noqa: E402
from core.services import retention as ret                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
# Two systems, each with its own data dir; where each "is" is set per test.
PKG = {"alpha": tempfile.mkdtemp(), "beta": tempfile.mkdtemp()}
PKG = {k: os.path.join(v, k) for k, v in PKG.items()}
for v in PKG.values():
    os.makedirs(v)
# <root>/data, so the default event archive (<root>/archive, beside it) is on
# the same drive -- as /mnt/nvme/cytron/<slug>/{data,archive} are on a box.
ROOTS = {k: tempfile.mkdtemp() for k in PKG}
DATA = {k: os.path.join(r, "data") for k, r in ROOTS.items()}
for d in DATA.values():
    os.makedirs(d)
# device per system: 1 = the eMMC (root), 2 = an NVMe, 3 = a USB stick,
# 4 = a fixed SATA disk; "broken" = config unreadable.
WHERE = {"alpha": 1, "beta": 1}
DEVINFO = {2: {"name": "nvme0n1", "removable": False, "usb": False},
           3: {"name": "sda", "removable": True, "usb": True},
           4: {"name": "sdb", "removable": False, "usb": False}}


def fake_dev(path):
    p = os.path.abspath(str(path))
    if p == os.path.abspath(os.sep):
        return 1
    for k, r in ROOTS.items():
        if p.startswith(os.path.abspath(r)):
            return WHERE[k]
    return 1


def fake_merged(pkg_dir):
    k = os.path.basename(pkg_dir)
    if WHERE.get(k) == "broken":
        raise ValueError("config.toml: bad TOML")
    return {}


ret._dev_of = fake_dev
ret._device_info = lambda dev, sys_root="/sys": DEVINFO.get(dev)
ret.dc._merged = fake_merged
ret.dc._data_dir = lambda pkg, cfg: DATA[os.path.basename(pkg)]
ret.remember_pkg_dirs([PKG["alpha"]])

# -- 1. eMMC -------------------------------------------------------------------------
ret._nvme_fitted = lambda sys_root="/sys": False
try:
    ret.set_policy({"clips_days": 365, "events_days": 30}, CFG)
    refused = ""
except ValueError as e:
    refused = str(e)
check("1 a system on the eMMC: a 365 is refused in words",
      refused.startswith("Video clips can be kept for up to 60 days")
      and "NVMe drive" in refused, refused)
pol = ret.set_policy({"clips_days": 60, "events_days": 30}, CFG)
t = ret.system_tier(PKG["alpha"])
check("1 ...60 applies, and 60 is accepted", t["limit_days"] == 60
      and pol["clips_days"] == 60 and pol["events_days"] == 30, (t, pol))
check("1 ...the reason says longer retention needs an NVMe",
      "on the built-in storage: up to 60 days" in t["reason"]
      and "Longer retention needs an NVMe drive" in t["reason"], t["reason"])

# -- 2. NVMe -----------------------------------------------------------------------------
WHERE["alpha"] = 2
t = ret.system_tier(PKG["alpha"])
pol = ret.set_policy({"clips_days": 365}, CFG)
check("2 the same system on a fixed NVMe: 365 applies",
      t["limit_days"] == 365 and ret.effective_policy(pol, t)["clips_days"] == 365, t)
check("2 ...said as the NVMe", "on the NVMe: up to 12 months" in t["reason"],
      t["reason"])

# -- 3. USB, and a fixed drive that is not an NVMe ------------------------------------------
WHERE["alpha"] = 3
t = ret.system_tier(PKG["alpha"])
check("3 a USB drive keeps 60, and says why",
      t["limit_days"] == 60 and "removable USB drive" in t["reason"]
      and "on a built-in NVMe drive" in t["reason"], t)
DEVINFO[3] = {"name": "sda", "removable": False, "usb": True}
check("3 ...a USB disk that calls itself fixed is still USB: 60",
      ret.system_tier(PKG["alpha"])["limit_days"] == 60)
WHERE["alpha"] = 4
t = ret.system_tier(PKG["alpha"])
check("3 a fixed SATA drive is not the NVMe: 60, and says so",
      t["limit_days"] == 60 and "a drive that is not an NVMe" in t["reason"],
      t["reason"])

# -- 4. two systems, two drives ------------------------------------------------------------------
WHERE.update(alpha=1, beta=2)
ret.remember_pkg_dirs(list(PKG.values()))
tiers = ret.system_tiers()
summary = ret.storage_tier(CFG)
check("4 each system keeps its own cap",
      tiers["alpha"]["limit_days"] == 60 and tiers["beta"]["limit_days"] == 365,
      tiers)
check("4 ...and the page is told they differ",
      summary["differs"] and summary["min_days"] == 60
      and summary["limit_days"] == 365, summary)
check("4 ...and says WHICH system keeps its data off the NVMe, and where",
      [x["system"] for x in summary["off_nvme"]] == ["alpha"]
      and summary["off_nvme"][0]["where"] and "built-in" in summary["off_nvme"][0]["on"],
      summary["off_nvme"])
now = time.time()
files = {}
for k in PKG:
    clips = os.path.join(DATA[k], "clips")
    os.makedirs(clips, exist_ok=True)
    for name, days in (("old", 70), ("young", 50)):
        path = os.path.join(clips, f"{name}.mp4")
        open(path, "wb").write(b"x")
        os.utime(path, (now - days * 86400, now - days * 86400))
        files[(k, name)] = path
ret.set_starts_at(int(now - 400 * 86400), CFG)
report = ret.sweep(list(PKG.values()), CFG, now=now)
check("4 the eMMC system's 70-day clip goes (its own 60-day rule)",
      not os.path.exists(files[("alpha", "old")]), report)
check("4 ...the NVMe system keeps its 70-day clip (365 applies there)",
      os.path.exists(files[("beta", "old")]), report)
check("4 ...and nothing younger than 60 days goes anywhere",
      os.path.exists(files[("alpha", "young")])
      and os.path.exists(files[("beta", "young")]), report)
check("4 the sweep reports what applied to each system",
      report.get("effective", {}).get("alpha", {}).get("clips_days") == 60
      and report["effective"]["beta"]["clips_days"] == 365, report.get("effective"))

# -- 5. unreadable config ------------------------------------------------------------------------
WHERE["beta"] = "broken"
t = ret.system_tier(PKG["beta"])
check("5 a system whose config cannot be read counts as eMMC (60), not skipped",
      t["limit_days"] == 60 and "could not be read" in t["reason"]
      and "beta" in ret.system_tiers(), t)
WHERE["beta"] = 2

# -- 6. a Save of another value ------------------------------------------------------------------
WHERE.update(alpha=1, beta=2)
ret.set_policy({"events_days": 20}, CFG)      # what the page now sends: a diff
check("6 a Save of another value leaves the stored 365 alone",
      ret.policy(CFG)["clips_days"] == 365, ret.policy(CFG))
panel = io.open(os.path.join(ROOT, "frontend", "src", "components",
                             "RetentionPanel.jsx"), encoding="utf-8").read()
check("6 the page sends only what the admin changed",
      "const diff = Object.fromEntries(Object.entries(edit)" in panel
      and 'editStart == null ? diff' in panel)

# -- 7. months; route; page ------------------------------------------------------------------------
lim = ret.limits(CFG)
check("7 the box maximums with one system on the NVMe: 365 days, 12 months",
      lim["clips_days"] == 365 and lim["history_months"] == 12
      and lim["events_months"] == 12, lim)
v = ret.view(CFG, list(PKG.values()))
check("7 the view carries chosen, applied per system, and why",
      v["policy"]["clips_days"] == 365
      and {s["slug"]: s["effective"]["clips_days"] for s in v["systems"]}
      == {"alpha": 60, "beta": 365}
      and all(s["reason"] for s in v["systems"]) and v["notice"] == [], v)
hub = io.open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8").read()
check("7 both retention routes answer with that view",
      hub.count("retention_service.view(st.config_dir") == 2)
check("7 the page shows chosen vs applied, and per-system caps where they differ",
      "chosen;" in panel
      and "storage?.differs" in panel and "t.effective.clips_days" in panel)


# -- 8. no systems yet: where a NEW system's data goes decides the box's answer --------
# (192.168.1.60, 2026-10-21: an NVMe was mounted, no system existed, and the
# notice said the data was not on it, because the stand-in was the store dir --
# where packages live, on the eMMC -- not where the app puts data.)
import os as _os
_real_ismount, _real_dev, _real_systems = _os.path.ismount, ret._dev_of, ret.system_tiers
ret.system_tiers = lambda pkg_dirs=None: {}
ret._dev_of = lambda path: (2 if str(path).replace("\\", "/").startswith("/mnt/nvme/") else 1)
_os.path.ismount = lambda p: str(p) == "/mnt/nvme"
st = ret.storage_tier(CFG)
check("8 no systems, an NVMe mounted: a new system's data would be on it, so no warning",
      st["extended"] is True and st["limit_days"] == 365, st)
_os.path.ismount = lambda p: False
st = ret.storage_tier(CFG)
check("8 no systems, no NVMe mounted: built-in storage, 60 days",
      st["extended"] is False and st["limit_days"] == 60, st)
_os.path.ismount, ret._dev_of, ret.system_tiers = _real_ismount, _real_dev, _real_systems
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all retention-tier checks passed")
