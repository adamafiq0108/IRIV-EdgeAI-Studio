# Snapshots have no count cap: the old 500 does not delete a photo any more.
#
# A system's config.toml written before this carries `keep_snapshots = 500`,
# which nobody chose; that exact value reads as unlimited. A deliberate other
# number still caps. Whatever the setting, the oldest go when the drive is
# below its free-space floor, and the file just written is never the victim.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import store as S                                    # noqa: E402

bad = 0


def check(msg, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + msg + ("" if ok else f"  {detail}"))
    bad += 0 if ok else 1


def count(st):
    return len(os.listdir(st.snap_dir))


check("0, nothing and the legacy 500 all mean unlimited",
      S.snapshot_cap(0) == 0 and S.snapshot_cap(None) == 0 and S.snapshot_cap(500) == 0)
check("a deliberate number still caps", S.snapshot_cap(5) == 5 and S.snapshot_cap(501) == 501)

st = S.Store(tempfile.mkdtemp(), keep_snapshots=500)
for i in range(620):
    st.save_snapshot(f"s{i:04d}", b"x")
check("620 photos with the legacy 500: all kept", count(st) == 620, count(st))

capped = S.Store(tempfile.mkdtemp(), keep_snapshots=5)
for i in range(9):
    capped.save_snapshot(f"s{i}", b"x")
check("a chosen cap of 5 keeps 5", count(capped) == 5, count(capped))

st.min_free_bytes = 1 << 60                     # the drive is always "full"
st.save_snapshot("newest", b"x")
left = os.listdir(st.snap_dir)
check("below the free-space floor the oldest go, the newest stays",
      left == ["newest.jpg"], left)

off = S.Store(tempfile.mkdtemp(), min_free_mb=0)
off.min_free_bytes = 0
for i in range(30):
    off.save_snapshot(f"s{i}", b"x")
check("floor 0 switches the space check off", count(off) == 30, count(off))

if bad:
    sys.exit(f"{bad} check(s) failed")
print("all snapshot cap checks passed")
