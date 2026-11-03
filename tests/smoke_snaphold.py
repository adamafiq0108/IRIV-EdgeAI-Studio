# A Snapshot node can HOLD its photo until the timer it reports is settled.
#
#   1. the store: a held photo is invisible to a listing of *.jpg until released
#   2. a restart (or a stay that never ends) shows what was waiting
#   3. the node's setting survives the output validator, and only on a Snapshot
#
#     py -3 tests/smoke_snaphold.py
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app.store import Store                                      # noqa: E402
from core.services import detector_control as dc                 # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def listed(store):
    return sorted(n for n in os.listdir(store.snap_dir) if n.endswith(".jpg"))


# -- 1. the store -----------------------------------------------------------------------
d = tempfile.mkdtemp()
store = Store(d)
os.makedirs(store.snap_dir, exist_ok=True)
store.save_snapshot("cap_a", b"x")
store.save_snapshot("cap_h", b"x", hold=True)
check("an ordinary photo is listed at once", listed(store) == ["cap_a.jpg"], listed(store))
check("...a held one is not, though it is on disk",
      os.path.exists(os.path.join(store.snap_dir, "cap_h.jpg.hold")))
check("releasing it shows it", store.release_snapshot("cap_h")
      and listed(store) == ["cap_a.jpg", "cap_h.jpg"], listed(store))
check("...and releasing what is not held is quietly false", not store.release_snapshot("cap_h")
      and not store.release_snapshot("nothing"))

# -- 2. a restart, or a stay that never ends --------------------------------------------
store.save_snapshot("cap_r1", b"x", hold=True)
store.save_snapshot("cap_r2", b"x", hold=True)
old = os.path.join(store.snap_dir, "cap_r1.jpg.hold")
os.utime(old, (time.time() - 7200, time.time() - 7200))
check("only the ones held longer than the limit are shown by age",
      store.release_held(older_than_s=3600) == 1
      and "cap_r1.jpg" in listed(store) and "cap_r2.jpg" not in listed(store), listed(store))
check("a restart shows the rest", store.release_held() == 1 and "cap_r2.jpg" in listed(store))
check("...and nothing is left held",
      not [n for n in os.listdir(store.snap_dir) if n.endswith(".hold")])

# -- 3. the node's setting ---------------------------------------------------------------
snap = dc._validate_output({"type": "snapshot", "trigger": "alert", "function": "f1",
                            "hold": True, "crop": True})
check("a Snapshot keeps `hold`", snap.get("hold") is True, snap)
off = dc._validate_output({"type": "snapshot", "trigger": "alert", "function": "f1"})
check("...and absent means off", "hold" not in off, off)
clip = dc._validate_output({"type": "record", "trigger": "alert", "function": "f1", "hold": True})
check("a clip has no such thing", "hold" not in clip, clip)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
