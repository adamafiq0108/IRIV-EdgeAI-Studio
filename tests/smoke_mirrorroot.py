# The IRIV-VS template mirrors never land in an EMPTY mount point.
#
# Tarong, 2026-10-05: templates._mirror_root checked only that /mnt/nvme is a
# folder. With the NVMe unmounted it still is one -- an empty directory on the
# eMMC -- so every published or saved template was mirrored onto the boot
# disk, under the very path the drive hides the moment it mounts again. The
# store already refuses an unmounted drive (core/config.py
# unmounted_mount_of, a48b238f); the mirrors now ask the same question and
# fall back to the store the same way.
#
# Pinned:
#   1. /mnt/nvme a folder with nothing mounted: the mirrors go to the store
#   2. the drive mounted there: the mirrors go to the drive
#   3. $CYTRON_IRIV_ROOT naming an ordinary folder is still taken at its word
#   4. a root that does not exist falls back as before
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE
os.environ.pop("CYTRON_IRIV_ROOT", None)

from core import config                                       # noqa: E402
from core.services import templates                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def inside(path, root):
    return os.path.normcase(os.path.abspath(path)).startswith(
        os.path.normcase(os.path.abspath(root)) + os.sep)


# A stand-in for /mnt/nvme: a real, empty folder, guarded as the NVMe's mount
# point because fstab lists it as one (an explicit $CYTRON_NVME on its own is
# taken at its word and not guarded; config.guarded_mounts).
NVME = tempfile.mkdtemp()
os.environ["CYTRON_NVME"] = NVME
templates._NVME_ROOT = NVME
config.FSTAB = os.path.join(tempfile.mkdtemp(), "fstab")
with open(config.FSTAB, "w", encoding="utf-8") as f:
    f.write(f"UUID=1 / ext4 defaults 0 1\nUUID=2 {NVME} ext4 defaults 0 2\n")
real_mounted = config.mounted

# 1. unmounted: a folder, not a drive
config.mounted = lambda p: False
pub = templates.public_templates_root()
dev = templates.dev_templates_root()
check("NVMe unmounted: published mirrors go to the store, not the empty mount point",
      inside(pub, STORE) and not inside(pub, NVME), pub)
check("...and so do a developer's saved ones",
      inside(dev, STORE) and not inside(dev, NVME), dev)
check("...nothing at all was created under the mount point",
      os.listdir(NVME) == [], os.listdir(NVME))

# 2. mounted: the drive is used
config.mounted = lambda p: os.path.normpath(str(p)) == os.path.normpath(NVME)
pub = templates.public_templates_root()
check("NVMe mounted: the mirrors go to the drive",
      inside(pub, NVME) and pub.endswith(os.path.join("IRIV-VS", "template")), pub)

# 3. an explicit root that is an ordinary folder is taken at its word
config.mounted = lambda p: False
OWN = tempfile.mkdtemp()
os.environ["CYTRON_IRIV_ROOT"] = OWN
pub = templates.public_templates_root()
check("$CYTRON_IRIV_ROOT on an ordinary folder is used", inside(pub, OWN), pub)

# ...but not when it points INTO the unmounted drive
os.environ["CYTRON_IRIV_ROOT"] = os.path.join(NVME, "iriv")
os.makedirs(os.environ["CYTRON_IRIV_ROOT"], exist_ok=True)
pub = templates.public_templates_root()
check("...and refused when it lives under the unmounted drive",
      inside(pub, STORE), pub)

# 4. a root that does not exist falls back as before
os.environ["CYTRON_IRIV_ROOT"] = os.path.join(OWN, "missing")
pub = templates.public_templates_root()
check("a missing root falls back to the store", inside(pub, STORE), pub)

config.mounted = real_mounted
if bad:
    print(f"\n{bad} check(s) failed")
    sys.exit(1)
print("\nall mirror-root checks passed")
