# The scripts that build a box never reach the box.
#
# WHAT THIS IS FOR. install.sh, setup_pi.sh and their neighbours turn a bare
# board into a working appliance. That is bench work at Cytron: a customer with
# a broken box sends it back, nobody rebuilds one in the field. Left on the
# appliance they are a one-command way to stand the software up on somebody
# else's hardware, so the installer keeps them off it.
#
# They are NOT what stops the software being copied -- the licence bound to the
# secure element does that, and tests/smoke_licensehw.py pins it. This closes
# the easy door and nothing more.
#
# WHY A TEST. The rule lives in two files that never see each other: the
# installer decides what a NEW box gets, and the release packer decides what an
# UPDATE carries. Miss the second and every box in the field is handed the
# scripts back on its next update -- silently, because an update that works is
# an update nobody looks at. This pins them together, and pins the other half
# too: the scripts a running box genuinely needs must NOT be on the list.
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


INSTALL = read("scripts/install.sh")
PACKER = read("scripts/dev/publish_release.py")

# -- the installer's list ------------------------------------------------------
m = re.search(r"^BENCH_ONLY=\(\n(.*?)^\)$", INSTALL, re.S | re.M)
check("install.sh declares BENCH_ONLY", m is not None)
if not m:
    sys.exit(1)
bench = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
check("...and it is not empty", len(bench) >= 6, repr(bench))

# A path that no longer exists makes the exclusion a no-op nobody notices --
# rsync does not complain about an --exclude that matches nothing.
missing = [p for p in bench if not os.path.exists(os.path.join(ROOT, p))]
check("every path on the list is a file that actually exists", not missing, repr(missing))

# -- the packer's list ---------------------------------------------------------
tree = ast.parse(PACKER)
excl = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "EXCLUDE_EXACT" for t in node.targets):
        excl = {ast.literal_eval(e) for e in node.value.elts}
check("publish_release.py declares EXCLUDE_EXACT", excl is not None)
if excl is None:
    sys.exit(1)

short = [p for p in bench if p not in excl]
check("an OTA cannot hand back what the installer kept off the box", not short,
      "missing from EXCLUDE_EXACT: " + repr(short))

# -- the other half: what a running box needs is NOT on the list ----------------
#
# Each of these is load-bearing on a box already in service. apply_update.py is
# copied out of the install tree by core/services/update.py to run an OTA, so a
# box that loses it can never update again -- including the update that would
# put it back. recover.py and db_console_role.py are installed to
# /usr/local/lib/irivai and run by systemd. The rest is config the box reads.
NEEDED = ("scripts/apply_update.py", "scripts/recover.py",
          # flip_link.py is the source the installer copies to
          # /usr/local/lib/irivai; a board that never received it cannot make
          # an update live when its install path sits under /opt.
          "scripts/flip_link.py",
          "scripts/db_console_role.py", "scripts/mediamtx/mediamtx.yml",
          "scripts/mediamtx/irivai-mediamtx.service",
          "scripts/udev/83-cytron-cam-slots.rules",
          "scripts/modprobe/irivai-uvcvideo.conf")
for path in NEEDED:
    check(f"{path} still reaches the box",
          path not in bench and path not in excl)
    check(f"...and {path} is still in the repo to reach it",
          os.path.exists(os.path.join(ROOT, path)))

# -- the installer does all three things, not just one --------------------------
#
# Excluding is not enough on its own: rsync's --delete pass only considers
# files it looked at, and an --exclude'd path is invisible to it, so a box
# installed by an EARLIER version of this script keeps its copies for ever
# unless they are removed outright first. Hence a delete, an exclude, and an
# assertion that the result held.
check("the installer deletes them from a target that already has them",
      'rm -f "${TARGET}/${bench}"' in INSTALL)
check("the installer builds an --exclude per entry",
      'BENCH_EXCLUDE+=(--exclude "$bench")' in INSTALL)
check("...and passes that array to rsync",
      '"${BENCH_EXCLUDE[@]}"' in INSTALL
      and INSTALL.index('BENCH_EXCLUDE+=') < INSTALL.index('"${BENCH_EXCLUDE[@]}"'))
check("...and refuses to finish if one survived anyway",
      '"${BENCH_ONLY[@]}"; do' in INSTALL
      and "reached the install -- refusing to continue" in INSTALL)

# -- and cannot delete itself out from under bash -------------------------------
#
# Deleting files from the target only became dangerous once the target could be
# the checkout the script is being READ from: bash reads a script as it runs,
# so a deleted install.sh takes the rest of the run with it.
check("installing a checkout onto itself is refused",
      "the source checkout and the install target are the same directory" in INSTALL)
check("...and that check happens before anything is deleted",
      INSTALL.index("the source checkout and the install target")
      < INSTALL.index('rm -rf "${TARGET}/core/dev"'))

# -- a message that would send somebody to a path that is not there -------------
check("the MediaMTX fallback warning says where to re-run it from",
      "FROM THE INSTALLER CHECKOUT" in INSTALL)

# -- the store lands on the NVMe, not beside the OS -------------------------------
# A freshly set-up drive is mounted root-owned; the service account could not
# create /mnt/nvme/cytron/store, so "first writable candidate" fell back to the
# home directory on the system disk. Seen on the first two customer boards.
check("the installer makes the NVMe store root writable by the service account",
      'install -d -m 2775 -o "$SERVICE_USER" -g "$SERVICE_USER" /mnt/nvme/cytron /mnt/nvme/cytron/store'
      in INSTALL)
check("...BEFORE the store root is first resolved, so the first answer is the drive",
      INSTALL.index("/mnt/nvme/cytron/store") < INSTALL.index("print(config.store_dir())"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
