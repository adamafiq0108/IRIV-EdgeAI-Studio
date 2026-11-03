# Pipi's review of the storage setup (2026-10-07), the three items fixed
# after 03eaea84:
#
#   1. MEDIUM: the box's OWN data drive, the one an active /etc/fstab line
#      names, could be formatted when it failed to mount at boot (it looks
#      exactly like a new unmounted drive). The helper refuses `format` for
#      it and still allows `mount` (the recovery); the hub labels it "This
#      box's storage drive, not mounted" and offers Mount only.
#   2. LOW: one setup or repair at a time on the box, whoever asks: a flock
#      on /run/irivai-storage-setup.lock, the second caller refused at once.
#   3. LOW: a forced repair (Try again) is one at a time and at least
#      REPAIR_MIN_S apart.
#
# Nothing real is touched: lsblk and every command go through a fake runner,
# the helper's machine is a stand-in Ctx, sudo is a fake.
#
#     python tests/smoke_storageguard.py
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading

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
UUID = "6f1c2a3b-0000-4000-8000-00000000abcd"


def part(name, size, fstype="", uuid=""):
    return {"name": name, "path": "/dev/" + name, "type": "part", "size": size,
            "fstype": fstype or None, "label": None, "uuid": uuid or None,
            "mountpoints": [None]}


def disk(name, size, children=(), serial=""):
    return {"name": name, "path": "/dev/" + name, "type": "disk", "size": size,
            "model": "Drive", "serial": serial or None, "wwn": None, "tran": "nvme",
            "rm": False, "fstype": None, "label": None, "uuid": None,
            "mountpoints": [None], "children": list(children)}


EMMC = {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 32 * GB,
        "mountpoints": [None], "children": [dict(part("mmcblk0p2", 31 * GB, "ext4"),
                                                 mountpoints=["/"])]}
# The box's own NVMe, listed in fstab, that did not mount this boot.
OWN = disk("nvme0n1", 512 * GB, [part("nvme0n1p1", 512 * GB, "ext4", UUID)], serial="OWN1")


class Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Fake:
    def __init__(self, tree):
        self.tree = json.loads(json.dumps(tree))
        self.calls = []

    def __call__(self, argv, input=None, timeout=None):
        self.calls.append(list(argv))
        if argv[0] == "lsblk":
            return Res(0, json.dumps({"blockdevices": self.tree}))
        if argv[0] == "blkid":
            return Res(0, UUID + "\n")
        return Res(0)


class Ctx(H.Ctx):
    def __init__(self, fstab_text, busy=False):
        tmp = tempfile.mkdtemp()
        self.fstab = os.path.join(tmp, "fstab")
        self.mount_point = "/mnt/nvme"
        self.busy = busy
        self.locked = 0
        with open(self.fstab, "w", encoding="utf-8") as fh:
            fh.write(fstab_text)

    def euid(self):
        return 0

    def service_user(self):
        return "cytron"

    def is_mounted(self, path):
        return False

    def mount_ro(self, path):
        return False

    def root_names(self):
        return ["mmcblk0", "mmcblk0p2"]

    def makedirs(self, path):
        pass

    def prepare_dir(self, path, user):
        pass

    def lock(self):
        if self.busy:
            raise H.Refused(H.BUSY)
        self.locked += 1
        return "held"

    def unlock(self, held):
        self.locked -= 1


ACTIVE = "PARTUUID=abc-02 / ext4 defaults 0 1\nUUID=%s /mnt/nvme ext4 defaults,nofail 0 2\n" % UUID
COMMENTED = "PARTUUID=abc-02 / ext4 defaults 0 1\n# UUID=%s /mnt/nvme ext4 defaults 0 2\n" % UUID
dev = H.describe(OWN)
fmt = {"action": "format", "disk": "nvme0n1", "id": dev["id"], "size": dev["size"],
       "confirm": "FORMAT"}
mnt = dict(fmt, action="mount", confirm="")

# -- 1. the box's own drive ----------------------------------------------------------
print("== 1. the box's own storage drive, not mounted")
check("fstab_uuids reads active lines only",
      H.fstab_uuids(ACTIVE) == {UUID} and H.fstab_uuids(COMMENTED) == set()
      and H.fstab_uuids("/dev/disk/by-uuid/%s /x ext4 d 0 2\n" % UUID) == {UUID})
run, ctx = Fake([EMMC, OWN]), Ctx(ACTIVE)
try:
    H.setup(fmt, run=run, ctx=ctx, out=io.StringIO())
    check("format of the drive fstab names is refused", False, "formatted")
except H.Refused as exc:
    writes = [c for c in run.calls if c[0] != "lsblk"]
    check("format of the drive fstab names is refused, nothing run",
          "own storage drive" in str(exc) and "Mount" in str(exc) and not writes,
          (str(exc), writes))
run, ctx = Fake([EMMC, OWN]), Ctx(ACTIVE)
out = H.setup(mnt, run=run, ctx=ctx, out=io.StringIO())
check("...Mount is still allowed: the recovery",
      out["action"] == "mount" and ["mount", "-t", "ext4", "-o", H.MOUNT_OPTS,
                                    "/dev/nvme0n1p1", "/mnt/nvme"] in run.calls, run.calls)
check("...and no wipefs/sfdisk/mkfs on that path",
      not {"wipefs", "sfdisk", "mkfs.ext4"} & {c[0] for c in run.calls})
run, ctx = Fake([EMMC, OWN]), Ctx(COMMENTED)
try:
    H.setup(fmt, run=run, ctx=ctx, out=io.StringIO())
except RuntimeError:
    pass                    # the fake lsblk shows no new partition after sfdisk
check("a commented-out line does not protect it: the format goes ahead",
      any(c[0] == "wipefs" for c in run.calls), run.calls)

os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())
from core import config as config_mod                                     # noqa: E402
from core.services import storage_job as J                                 # noqa: E402
from core.services import storage_setup as S                               # noqa: E402

cfg = tempfile.mkdtemp()
config_mod.mounted = lambda p: False
config_mod.FSTAB = os.path.join(cfg, "fstab")
with open(config_mod.FSTAB, "w", encoding="utf-8") as fh:
    fh.write(ACTIVE)
found = S.scan(Fake([EMMC, OWN]), ["mmcblk0", "mmcblk0p2"])
d = found[0] if found else {}
check("the hub labels it", d.get("expected") is True
      and d.get("expected_label") == "This box's storage drive, not mounted", d)
p = S.prompt(cfg, found) or {}
check("...and the popup still asks (to mount it back)", p.get("expected") is True)
try:
    J.start(d["id"], True, "FORMAT", config_dir=cfg, run=Fake([EMMC, OWN]),
            root_names=["mmcblk0", "mmcblk0p2"], popen=lambda *a, **k: None)
    check("the hub refuses to start a format of it", False, "started")
except ValueError as exc:
    check("the hub refuses to start a format of it",
          "never formatted" in str(exc) and J.job()["state"] != "running", str(exc))
ui = open(os.path.join(ROOT, "frontend", "src", "components", "NewStoragePrompt.jsx"),
          encoding="utf-8").read()
check("the popup shows the label and hides Format for it",
      "dev.expected_label" in ui and "{!dev.expected && (" in ui
      and 'dev.expected ? "Mount it"' in ui)

# -- 2. one at a time on the box ------------------------------------------------------
print("== 2. the box-wide lock")
run, ctx = Fake([EMMC, OWN]), Ctx(ACTIVE, busy=True)
for req in (mnt, {"action": "repair"}):
    try:
        H.setup(req, run=run, ctx=ctx, out=io.StringIO())
        check("a second caller (%s) is refused at once" % req["action"], False, "ran")
    except H.Refused as exc:
        check("a second caller (%s) is refused at once, nothing run" % req["action"],
              str(exc) == H.BUSY and not run.calls, (str(exc), run.calls))
ctx = Ctx(ACTIVE)
H.setup(mnt, run=Fake([EMMC, OWN]), ctx=ctx, out=io.StringIO())
check("the lock is released after a run", ctx.locked == 0)
ctx = Ctx(ACTIVE)
try:
    H.setup(fmt, run=Fake([EMMC, OWN]), ctx=ctx, out=io.StringIO())
except H.Refused:
    pass
check("...and after a refusal", ctx.locked == 0)
check("the lock lives in /run", H.LOCK_PATH == "/run/irivai-storage-setup.lock")
if os.name == "posix":
    real1, real2 = H.Ctx(), H.Ctx()
    real1.lock_path = real2.lock_path = os.path.join(tempfile.mkdtemp(), "s.lock")
    held = real1.lock()
    try:
        real2.lock()
        check("a real flock: the second taker is refused", False, "taken twice")
    except H.Refused as exc:
        check("a real flock: the second taker is refused", str(exc) == H.BUSY)
    real1.unlock(held)
    real1.unlock(real2.lock())
    check("...and it is free again once released", True)

# -- 3. forced repair, rate-limited -----------------------------------------------------
print("== 3. Try again: one at a time, REPAIR_MIN_S apart")
config_mod.mounted = lambda p: True
calls = []


def sudo(argv, input=None):
    calls.append(1)
    return Res(0)


J._repair.update(at=0.0, result=None, busy=False)
J.repair(force=True, run=sudo)
J.repair(force=True, run=sudo)
check("two forced repairs in a row run the helper once", len(calls) == 1, calls)
J._repair["at"] -= J.REPAIR_MIN_S + 1
J.repair(force=True, run=sudo)
check("...a forced one after REPAIR_MIN_S runs again", len(calls) == 2, calls)
check("REPAIR_MIN_S is about five seconds", 3 <= J.REPAIR_MIN_S <= 10)

gate, entered = threading.Event(), threading.Event()


def slow(argv, input=None):
    calls.append(1)
    entered.set()
    gate.wait(5)
    return Res(0)


J._repair.update(at=0.0, result={"ok": True, "reason": ""}, busy=False)
t = threading.Thread(target=J.repair, kwargs={"force": True, "run": slow})
t.start()
entered.wait(5)
n = len(calls)
second = J.repair(force=True, run=slow)
check("a forced repair while one runs does not start another",
      len(calls) == n and second["ok"] is False and "running" in second["reason"],
      (calls, second))
gate.set()
t.join(5)
check("...and the flag clears when it ends", J._repair["busy"] is False)

print("FAILED: %d" % bad if bad else "all passed")
sys.exit(1 if bad else 0)
