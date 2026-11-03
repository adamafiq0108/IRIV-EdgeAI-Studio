# -*- coding: utf-8 -*-
# smoke_storemount.py -- the store root tells the truth about an NVMe that is
# promised by fstab and not mounted.
#
# On one board the NVMe's fstab line carried a UUID that no longer matched the
# drive, so /mnt/nvme was a plain folder on the eMMC. store_dir() fell back to
# the home directory (right) and said the preferred /mnt/nvme/cytron/store was
# "empty" (wrong, and misleading: it sent the operator looking for lost content
# instead of at the mount). Checks:
#   * a candidate under an UNMOUNTED mount point is never adopted, even when
#     writable (it would plant the store on the system disk in disguise)
#   * the fallback line says "NOT mounted", not "empty"
#   * the fstab-listed-but-unmounted state gets its own boot-time line
#   * a mounted drive (ismount True) keeps the old "is empty" wording
import io
import os
import pathlib
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("CYTRON_STORE", None)

from core import config                                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


tmp = pathlib.Path(tempfile.mkdtemp())
nvme = tmp / "mnt" / "nvme"
home = tmp / "home" / "store"
(home / "systems" / "demo").mkdir(parents=True)        # the real, populated store
(nvme / "cytron" / "store").mkdir(parents=True)        # writable, on the eMMC
fstab = tmp / "fstab"
fstab.write_text("# comment\n"
                 f"UUID=dead-beef {nvme} ext4 defaults,nofail 0 2\n",
                 encoding="utf-8")

saved = (config.STORE_CANDIDATES, config.NVME_MOUNT, config.FSTAB,
         os.path.ismount)
config.STORE_CANDIDATES = [str(nvme / "cytron" / "store"), str(home)]
config.NVME_MOUNT = str(nvme)
config.FSTAB = str(fstab)


def resolve(mounted):
    """store_dir() from cold, with the mount point reporting `mounted`."""
    config._store_cache.clear()
    config._store_said.clear()
    os.path.ismount = lambda p: mounted and str(p) == str(nvme)
    err, sys.stderr = sys.stderr, io.StringIO()
    try:
        chosen = config.store_dir()
        return chosen, sys.stderr.getvalue()
    finally:
        sys.stderr = err


try:
    chosen, said = resolve(mounted=False)
    check("unmounted NVMe: the populated home store is used",
          chosen == home, (chosen, said))
    check("...and the line says the preferred root is NOT mounted, not empty",
          "is NOT mounted" in said and "is empty" not in said, said)
    check("...with a boot-time line naming fstab and the fix",
          "listed in" in said and "NOT mounted" in said and "mount -a" in said,
          said)

    # Nothing populated anywhere and the NVMe unmounted: the fresh-box rule
    # must still not land on the disguised folder.
    (home / "systems" / "demo").rmdir()
    for marker in (home / config.STORE_MARKER, nvme / "cytron" / "store" / config.STORE_MARKER):
        if marker.exists():
            marker.unlink()
    chosen, said = resolve(mounted=False)
    check("fresh box, unmounted NVMe: the home candidate wins",
          chosen == home, (chosen, said))

    # Mounted (as far as the resolver can tell): the old words come back.
    (home / "systems" / "demo").mkdir(parents=True)
    (home / config.STORE_MARKER).unlink(missing_ok=True)
    (nvme / "cytron" / "store" / config.STORE_MARKER).unlink(missing_ok=True)
    chosen, said = resolve(mounted=True)
    check("mounted but empty NVMe beside a populated home: 'is empty' stays",
          chosen == home and "is empty" in said and "NOT mounted" not in said,
          said)

    # fstab without the mount point: no mount line at all.
    fstab.write_text("UUID=1 / ext4 defaults 0 1\n", encoding="utf-8")
    chosen, said = resolve(mounted=False)
    check("not in fstab: the resolver does not claim a promise fstab never made",
          "listed in" not in said, said)
finally:
    (config.STORE_CANDIDATES, config.NVME_MOUNT, config.FSTAB,
     os.path.ismount) = saved

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all store-mount checks passed")
