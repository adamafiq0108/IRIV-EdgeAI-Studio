# "Is there second storage" must ask about the place writes actually go.
#
# Captures and recordings are refused without a second drive, because filling
# the media the OS boots from is how a recorder bricks a box. The test for it
# wrote a probe file at the MOUNT ROOT -- and a normally installed board has
# /mnt/nvme owned root:root 755, which is correct: a service account has no
# business writing at the root of a disk. Everything this software writes goes
# under one directory it owns.
#
# So the probe asked for a permission the service account is not supposed to
# have, failed, and reported "no second storage" on a box with a healthy
# 117 GB drive mounted (192.168.1.234). The visible symptom: the Snapshots and
# Clips switches greyed out, with a tooltip telling the operator to fit the
# NVMe they had already fitted.
#
# Ownership cannot be faked in a test without root, so the root is made
# unwritable by CHMOD, which is the same question the probe asks.
import os
import pathlib
import stat
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "systems" / "demo"))

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


if os.name != "posix" or os.geteuid() == 0:
    # chmod means nothing to root, and Windows has no mode bits to remove.
    print("skipped: needs a posix box and a non-root user")
    raise SystemExit(77)

tmp = tempfile.mkdtemp()
mount = os.path.join(tmp, "nvme")
os.makedirs(os.path.join(mount, "cytron"))
os.environ["CYTRON_NVME"] = mount

import app.config as appcfg                                     # noqa: E402

check("the owned directory is named once", appcfg.NVME_DIR == "cytron",
      appcfg.NVME_DIR)

print("== a drive whose root the service account cannot write")
os.chmod(mount, stat.S_IRUSR | stat.S_IXUSR)                    # r-x------
try:
    check("the root really is unwritable",
          not os.access(mount, os.W_OK))
    check("the drive is still usable -- the writes go under it, not at it",
          appcfg._nvme_usable() == mount, str(appcfg._nvme_usable()))
finally:
    os.chmod(mount, 0o755)

print("== and the owned directory itself is what gets tested")
os.chmod(os.path.join(mount, "cytron"), stat.S_IRUSR | stat.S_IXUSR)
try:
    check("an unwritable target IS refused -- that is the real fault",
          appcfg._nvme_usable() is None, str(appcfg._nvme_usable()))
finally:
    os.chmod(os.path.join(mount, "cytron"), 0o755)

print("== a fresh drive, before the directory exists")
# Nothing to own yet, so "can I write here" is the honest question: creating
# it is the next thing that has to happen.
os.rmdir(os.path.join(mount, "cytron"))
check("a writable bare drive is usable", appcfg._nvme_usable() == mount)
os.chmod(mount, stat.S_IRUSR | stat.S_IXUSR)
try:
    check("an unwritable bare drive is not",
          appcfg._nvme_usable() is None, str(appcfg._nvme_usable()))
finally:
    os.chmod(mount, 0o755)

print("== no drive at all")
os.environ["CYTRON_NVME"] = os.path.join(tmp, "does-not-exist")
import importlib                                                # noqa: E402
importlib.reload(appcfg)
check("a missing mount point is not storage", appcfg._nvme_usable() is None)

# The probe file must never be left behind: it is written into a directory the
# operator sees, on every status poll, from several processes at once.
os.environ["CYTRON_NVME"] = mount
importlib.reload(appcfg)
appcfg._nvme_usable()
left = [p for p in os.listdir(os.path.join(mount, "cytron"))
        if p.startswith(".cytron_write_test")] if os.path.isdir(
            os.path.join(mount, "cytron")) else []
left += [p for p in os.listdir(mount) if p.startswith(".cytron_write_test")]
check("the probe cleans up after itself", not left, str(left))

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
