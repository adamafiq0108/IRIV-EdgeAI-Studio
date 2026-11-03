# "Use as it is" on a drive that already holds ANOTHER box's IRIV software
# is refused (Tok Aba 2026-10-07): two boxes' systems, layouts and workflows
# would meet in one store. The popup says so before the click and offers
# Format only; the helper refuses again right before the mount.
#
# Pinned:
#   * the look is read-only: mount -o ro,nosuid,nodev,noexec on a fresh
#     folder under the probe base, unmounted again, the folder removed;
#   * cytron/store on the drive -> inspect says foreign with Tok Aba's words,
#     and `mount` is refused with them before the real mount is attempted;
#   * the box's own drive (UUID on an active fstab line) is NOT looked at
#     and NOT refused: mounting it back is the recovery;
#   * a drive with other data (no cytron/store) mounts as before;
#   * Format is never refused for it; the hub's inspect() and the popup.
#
# Nothing real is mounted: the runner is a fake that "mounts" by making the
# folders a drive would show.
#
#     python tests/smoke_storageforeign.py
import importlib.util
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


spec = importlib.util.spec_from_file_location(
    "storage_setup_helper", os.path.join(ROOT, "scripts", "storage_setup.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

GB = 1000 ** 3
UUID = "6f1c2a3b-0000-4000-8000-00000000beef"
EMMC = {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 32 * GB,
        "mountpoints": [None], "children": [{"name": "mmcblk0p2", "path": "/dev/mmcblk0p2",
                                             "type": "part", "size": 31 * GB,
                                             "fstype": "ext4", "mountpoints": ["/"]}]}
DRIVE = {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 256 * GB,
         "model": "Drive", "serial": "FOR1", "wwn": None, "tran": "usb", "rm": False,
         "fstype": None, "label": None, "uuid": None, "mountpoints": [None],
         "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part",
                       "size": 256 * GB, "fstype": "ext4", "label": None,
                       "uuid": UUID, "mountpoints": [None]}]}


class Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Fake:
    """A read-only probe mount makes the drive's top folders appear in the
    probe folder; `holds` says whether cytron/store is among them."""

    def __init__(self, holds):
        self.holds = holds
        self.calls = []

    def __call__(self, argv, input=None, timeout=None):
        self.calls.append(list(argv))
        if argv[0] == "lsblk":
            return Res(0, json.dumps({"blockdevices": [EMMC, DRIVE]}))
        if argv[0] == "blkid":
            return Res(0, UUID + "\n")
        if argv[0] == "mount" and H.PROBE_OPTS in argv:
            os.makedirs(os.path.join(argv[-1], "photos"))
            if self.holds:
                os.makedirs(os.path.join(argv[-1], "cytron", "store", "systems"))
        if argv[0] == "umount" and argv[-1].startswith(probe):
            import shutil
            shutil.rmtree(argv[-1], ignore_errors=True)
            os.makedirs(argv[-1])
        return Res(0)


probe = tempfile.mkdtemp()


class Ctx(H.Ctx):
    def __init__(self, fstab_text=""):
        self.fstab = os.path.join(tempfile.mkdtemp(), "fstab")
        self.mount_point = "/mnt/nvme"
        self.probe_base = probe
        with open(self.fstab, "w", encoding="utf-8") as fh:
            fh.write(fstab_text)

    def euid(self):
        return 0

    def service_user(self):
        return "cytron"

    def is_mounted(self, path):
        return False

    def root_names(self):
        return ["mmcblk0", "mmcblk0p2"]

    def makedirs(self, path):
        pass

    def prepare_dir(self, path, user):
        pass

    def lock(self):
        return None

    def unlock(self, held):
        pass


dev = H.describe(DRIVE)
req = {"disk": "sda", "id": dev["id"], "size": dev["size"]}
OWN = "UUID=%s /mnt/nvme ext4 defaults,nosuid,nodev,nofail 0 2\n" % UUID


def mounts(run):
    return [c for c in run.calls if c[0] == "mount"]


print("== another box's drive")
run = Fake(holds=True)
out = H.setup(dict(req, action="inspect"), run=run, ctx=Ctx(), out=io.StringIO())
check("inspect: foreign, in Tok Aba's words",
      out["foreign"] is True and out["message"] == "This drive already holds IRIV "
      "software from another box. Format it to use it here.", out)
m = mounts(run)
check("...the look is one read-only, nosuid, nodev, noexec mount under the probe base",
      len(m) == 1 and m[0][:5] == ["mount", "-t", "ext4", "-o", "ro,nosuid,nodev,noexec"]
      and m[0][5] == "/dev/sda1" and m[0][6].startswith(probe), m)
check("...unmounted again and its folder removed",
      ["umount", m[0][6]] in run.calls and not os.path.exists(m[0][6]))
check("...and nothing written: no wipefs, mkfs, fstab or folders",
      not {"wipefs", "sfdisk", "mkfs.ext4", "systemctl"} & {c[0] for c in run.calls})
run = Fake(holds=True)
try:
    H.setup(dict(req, action="mount"), run=run, ctx=Ctx(), out=io.StringIO())
    check("use as it is: refused", False, "mounted")
except H.Refused as exc:
    check("use as it is: refused with the same words, before the real mount",
          str(exc) == H.FOREIGN and not [c for c in mounts(run) if "/mnt/nvme" in c],
          (str(exc), run.calls))

print("== the exceptions")
run = Fake(holds=True)
out = H.setup(dict(req, action="mount"), run=run, ctx=Ctx(OWN), out=io.StringIO())
check("the box's own drive (active fstab line) is not looked at and mounts back",
      out["action"] == "mount" and len(mounts(run)) == 1
      and mounts(run)[0][-1] == "/mnt/nvme", run.calls)
out = H.setup(dict(req, action="inspect"), run=Fake(holds=True), ctx=Ctx(OWN),
              out=io.StringIO())
check("...and inspect says not foreign for it", out["foreign"] is False)
run = Fake(holds=False)
out = H.setup(dict(req, action="mount"), run=run, ctx=Ctx(), out=io.StringIO())
check("a drive with other data (no cytron/store) mounts as before",
      out["action"] == "mount" and mounts(run)[-1][-1] == "/mnt/nvme", run.calls)
src = open(os.path.join(ROOT, "scripts", "storage_setup.py"), encoding="utf-8").read()
check("Format is never refused for it (only mount checks FOREIGN)",
      'if action == "mount" and not dev["expected"] and ctx.holds_iriv(' in src)

print("== the hub and the popup")
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())
from core import config as config_mod                                     # noqa: E402
from core.services import storage_setup as S                               # noqa: E402

config_mod.mounted = lambda p: False
config_mod.FSTAB = os.path.join(tempfile.mkdtemp(), "fstab")
open(config_mod.FSTAB, "w").close()
sent = []


def sudo(argv, input=None):
    sent.append(json.loads(input))
    return Res(0, 'STEP 0 0 x\nDONE {"action": "inspect", "foreign": true, "message": "%s"}\n'
               % H.FOREIGN)


class Both:
    """lsblk for scan(), the helper for the inspect call."""

    def __call__(self, argv, input=None, timeout=None):
        if argv[0] == "lsblk":
            return Res(0, json.dumps({"blockdevices": [EMMC, DRIVE]}))
        return sudo(argv, input)


got = S.inspect(dev["id"], run=Both(), root_names=["mmcblk0", "mmcblk0p2"])
check("inspect() asks the helper with the drive's identity and passes its answer on",
      got == {"foreign": True, "message": H.FOREIGN}
      and sent == [{"action": "inspect", "disk": "sda", "id": dev["id"], "size": dev["size"]}],
      (got, sent))
S.inspect(dev["id"], run=Both(), root_names=["mmcblk0", "mmcblk0p2"])
check("...remembered per drive: one look", len(sent) == 1)
with open(config_mod.FSTAB, "w", encoding="utf-8") as fh:
    fh.write(OWN)
S._inspected.clear()
got = S.inspect(dev["id"], run=Both(), root_names=["mmcblk0", "mmcblk0p2"])
check("the box's own drive needs no look", got["foreign"] is False and len(sent) == 1)
ui = open(os.path.join(ROOT, "frontend", "src", "components", "NewStoragePrompt.jsx"),
          encoding="utf-8").read()
check("the popup looks before offering use-as-it-is, and then offers Format only",
      "/api/hub/storage/inspect?id=" in ui and "{!look?.foreign && (" in ui
      and "{look.message}" in ui)
check("no em dash in the new code", all(chr(0x2014) not in open(
    os.path.join(ROOT, *p.split("/")), encoding="utf-8").read() for p in (
    "scripts/storage_setup.py", "core/services/storage_setup.py",
    "core/routes/storage_setup.py", "frontend/src/components/NewStoragePrompt.jsx")))

print("FAILED: %d" % bad if bad else "all passed")
sys.exit(1 if bad else 0)
