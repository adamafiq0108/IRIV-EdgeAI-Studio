# "New storage detected" and the drive's folders (Tok Aba 2026-10-07): which
# drives may be offered, what the root helper runs to format, mount or
# repair, the admin's No, and when the "Storage is not writable" card shows.
#
# Nothing real is touched: lsblk and every command go through a fake runner
# that records argv, the helper's machine (fstab, the mount point, root, the
# folders it prepares) is a stand-in Ctx, sudo is a fake, and the hub's
# memory is a temp config dir.
#
# Pinned:
#   * the trigger is MOUNT STATE: a drive with anything of it mounted (at
#     /mnt/nvme, under /media, anywhere) is never offered; one with nothing
#     mounted is;
#   * the disk holding / (by mount point AND by kernel name) is never
#     offered, nor mmcblk / zram / loop, nor a disk with LVM;
#   * format runs wipefs, sfdisk (one GPT partition), mkfs.ext4 -L
#     IRIV-DATA, blkid, mount at /mnt/nvme, fstab by UUID with nofail,
#     daemon-reload, then the box's folders -- in that order, and only with
#     FORMAT typed and the same drive;
#   * no-format on a drive with no Linux filesystem is refused before any
#     command, by the helper and by the hub; on ext4 it touches nothing but
#     the box's four folders (no chown -R anywhere);
#   * repair: a mounted drive gets its folders; a read-only mount is
#     reported as such and never remounted; the hub repairs a mounted drive
#     by itself and never offers it;
#   * the dataset card shows only after a repair that failed, with its
#     reason; a repair that worked shows no card;
#   * No is remembered for that drive only; another drive asks;
#   * install.sh, upgrade_steps.sh and setup_nvme.sh use the one helper.
#
#     python tests/smoke_storagesetup.py
import importlib.util
import io
import json
import os
import re
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


def part(name, size, fstype="", label="", uuid="", mps=()):
    return {"name": name, "path": "/dev/" + name, "type": "part", "size": size,
            "fstype": fstype or None, "label": label or None, "uuid": uuid or None,
            "mountpoints": list(mps) or [None]}


def disk(name, size, children=(), model="Drive", serial="", wwn="", tran="", rm=False,
         fstype="", uuid="", mps=()):
    d = {"name": name, "path": "/dev/" + name, "type": "disk", "size": size,
         "model": model, "serial": serial or None, "wwn": wwn or None,
         "tran": tran or None, "rm": rm, "fstype": fstype or None, "label": None,
         "uuid": uuid or None, "mountpoints": list(mps) or [None]}
    if children:
        d["children"] = list(children)
    return d


EMMC = disk("mmcblk0", 32 * GB, [part("mmcblk0p1", GB // 2, "vfat", mps=["/boot/firmware"]),
                                 part("mmcblk0p2", 31 * GB, "ext4", mps=["/"])])
NVME_BOOT = disk("nvme1n1", 256 * GB, [part("nvme1n1p1", GB // 2, "vfat", mps=["/boot/firmware"]),
                                       part("nvme1n1p2", 255 * GB, "ext4", mps=["/"])],
                 serial="BOOT1", tran="nvme")
NVME_NEW = disk("nvme0n1", 512 * GB, serial="S5NEW", tran="nvme", model="Samsung 980")
USB_FAT = disk("sda", 64 * GB, [part("sda1", 64 * GB, "vfat", "STICK", "AB-CD")],
               serial="USB1", tran="usb", rm=True, model="SanDisk")
USB_MOUNTED = disk("sdh", 64 * GB, [part("sdh1", 64 * GB, "vfat", "PHOTOS", "EF-01",
                                         mps=["/media/pi/PHOTOS"])],
                   serial="USB3", tran="usb", rm=True)
USB_EXT = disk("sdb", 128 * GB, [part("sdb1", 128 * GB, "ext4", "OLD", "1111-ext4")],
               serial="USB2", tran="usb", model="Kingston")
ZRAM = disk("zram0", 2 * GB, mps=["[SWAP]"])
LOOP = {"name": "loop0", "type": "loop", "size": GB}
HOME_DISK = disk("sdc", 500 * GB, [part("sdc1", 500 * GB, "ext4", uuid="h", mps=["/home"])],
                 serial="H1")
LVM_DISK = disk("sdd", 500 * GB, [dict(part("sdd1", 500 * GB, "LVM2_member"),
                                       children=[{"name": "vg-lv", "type": "lvm",
                                                  "size": 500 * GB}])], serial="L1")
MMC_EXTRA = disk("mmcblk1", 64 * GB, [part("mmcblk1p1", 64 * GB, "ext4", uuid="sd")])
STORE = disk("sdf", 200 * GB, [part("sdf1", 200 * GB, "ext4", uuid="s", mps=["/mnt/nvme"])],
             serial="S")

# -- 1. what may be offered --------------------------------------------------------
print("== eligibility: the trigger is mount state")
tree = [EMMC, NVME_NEW, USB_FAT, USB_MOUNTED, ZRAM, LOOP, HOME_DISK, LVM_DISK, MMC_EXTRA, STORE]
offered, refused = H.eligible(tree, root_names=["mmcblk0p2", "mmcblk0"])
names = [d["name"] for d in offered]
why = dict(refused)
check("only the drives with nothing mounted are offered", names == ["nvme0n1", "sda"], names)
check("a drive mounted under /media is ready as it is, not offered",
      why.get("sdh", "").startswith("already mounted"), why.get("sdh"))
check("a drive mounted at /mnt/nvme is the box's storage, not offered",
      why.get("sdf") == "already the box's storage", why.get("sdf"))
check("the eMMC holding / is excluded", why.get("mmcblk0", "").startswith("the built-in"),
      why.get("mmcblk0"))
check("a second mmcblk (SD card) is excluded too", why.get("mmcblk1", "").startswith("the built-in"))
check("zram and loop are excluded", "zram0" in why and "loop0" in why)
check("a disk holding /home is a system disk", why.get("sdc") == "the disk the system runs from")
check("LVM is excluded", "LVM" in why.get("sdd", ""), why.get("sdd"))
offered, refused = H.eligible([NVME_BOOT, NVME_NEW], root_names=[])
check("an NVMe the box BOOTS from is excluded by its / mount",
      [d["name"] for d in offered] == ["nvme0n1"]
      and dict(refused).get("nvme1n1") == "the disk the system runs from", refused)
hidden_root = disk("sde", 200 * GB, [part("sde1", 200 * GB, "ext4", uuid="r")], serial="R")
offered, refused = H.eligible([hidden_root], root_names=["sde1"])
check("...and by kernel name when lsblk does not show / (overlay root)",
      not offered and dict(refused).get("sde") == "the disk the system runs from", refused)

usb = H.describe(USB_FAT)
check("a vfat stick cannot be used as it is, and says why",
      usb["usable"] is None and "vfat" in usb["mount_refusal"] and "Format" in usb["mount_refusal"])
blank = H.describe(NVME_NEW)
check("an empty drive says it has no filesystem",
      blank["usable"] is None and "no filesystem" in blank["mount_refusal"])
check("identity is WWN/serial, not the slot", blank["id"] == "serial:S5NEW"
      and H.device_key(dict(NVME_NEW, wwn="eui.1")) == "wwn:eui.1")


# -- the helper's machine ----------------------------------------------------------
class Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Fake:
    """lsblk answers from `tree`; sfdisk gives the disk one new partition;
    blkid answers a UUID; everything else succeeds and is recorded."""

    def __init__(self, tree):
        self.tree = json.loads(json.dumps(tree))
        self.calls = []

    def __call__(self, argv, input=None, timeout=None):
        self.calls.append(list(argv))
        if argv[0] == "lsblk":
            return Res(0, json.dumps({"blockdevices": self.tree}))
        if argv[0] == "sfdisk":
            self.sfdisk_input = input
            for d in self.tree:
                if d["path"] == argv[-1]:
                    d["children"] = [part(d["name"] + ("p1" if d["name"][-1].isdigit() else "1"),
                                          d["size"] - 2 ** 20)]
        if argv[0] == "blkid":
            return Res(0, "9f1c-new-uuid\n")
        return Res(0)


class Ctx(H.Ctx):
    def __init__(self, tmp, mounted=False, ro=False):
        self.fstab = os.path.join(tmp, "fstab")
        self.mount_point = "/mnt/nvme"
        self._mounted, self._ro = mounted, ro
        self.prepared = []
        with open(self.fstab, "w", encoding="utf-8") as fh:
            fh.write("proc /proc proc defaults 0 0\n"
                     "PARTUUID=abc-02 / ext4 defaults,noatime 0 1\n"
                     "UUID=old-gone /mnt/nvme ext4 defaults,nofail 0 2\n")

    def euid(self):
        return 0

    def service_user(self):
        return "cytron"

    def is_mounted(self, path):
        return self._mounted

    def mount_ro(self, path):
        return self._ro

    def root_names(self):
        return ["mmcblk0", "mmcblk0p2"]

    def makedirs(self, path):
        pass

    def prepare_dir(self, path, user):
        self.prepared.append((path, user))

    def lock(self):                 # the box-wide flock: tests/smoke_storageguard.py
        return None

    def holds_iriv(self, run, part, fstype):    # tests/smoke_storageforeign.py
        return False

    def unlock(self, held):
        pass

    def sleep(self, s):
        pass


APP = [("/mnt/nvme/" + d, "cytron") for d in ("cytron", "dataset_image", "dataset_rec", "model")]

# -- 2. format -----------------------------------------------------------------------
print("== format")
tmp = tempfile.mkdtemp()
tree = [EMMC, NVME_NEW, USB_FAT]
req = {"action": "format", "disk": "sda", "id": usb["id"], "size": usb["size"],
       "confirm": "FORMAT"}
run, ctx, out = Fake(tree), Ctx(tmp), io.StringIO()
done = H.setup(req, run=run, ctx=ctx, out=out)
argv = [" ".join(c) for c in run.calls if c[0] != "lsblk"]
want = ["wipefs -a /dev/sda1", "wipefs -a /dev/sda",
        "sfdisk --wipe always --wipe-partitions always /dev/sda", "udevadm settle",
        "mkfs.ext4 -F -L IRIV-DATA /dev/sda1", "udevadm settle",
        "blkid -s UUID -o value /dev/sda1", "mount -t ext4 -o nosuid,nodev /dev/sda1 /mnt/nvme",
        "systemctl daemon-reload"]
check("the format command sequence, in order", argv == want, "\n      ".join(argv))
check("one GPT partition of type Linux", run.sfdisk_input == "label: gpt\n,,L\n",
      repr(getattr(run, "sfdisk_input", None)))
check("then the box's four folders, for the service account", ctx.prepared == APP, ctx.prepared)
fstab = open(ctx.fstab, encoding="utf-8").read()
check("fstab: one line by UUID with nofail at /mnt/nvme",
      "UUID=9f1c-new-uuid  /mnt/nvme  ext4  defaults,nosuid,nodev,nofail,x-systemd.device-timeout=10s  0  2"
      in fstab)
check("...the old /mnt/nvme line commented out, the root line untouched",
      "# irivai storage-setup" in fstab and "replaced: UUID=old-gone" in fstab
      and "\nPARTUUID=abc-02 / ext4 defaults,noatime 0 1\n" in fstab)
check("...and the previous fstab kept", os.path.exists(ctx.fstab + ".irivai-bak"))
check("progress lines for every step, then the result",
      out.getvalue().count("STEP ") == 7 and done["uuid"] == "9f1c-new-uuid"
      and done["label"] == "IRIV-DATA", out.getvalue())

print("== refusals (nothing written)")


def refused_with(req, ctx, tree, needle):
    run = Fake(tree)
    try:
        H.setup(req, run=run, ctx=ctx, out=io.StringIO())
    except H.Refused as exc:
        writes = [c for c in run.calls if c[0] != "lsblk"]
        return (needle in str(exc) and not writes and not ctx.prepared,
                str(exc) + " " + repr(writes))
    return False, "not refused"


ok, why_ = refused_with(dict(req, confirm="format please"), Ctx(tmp), tree, "FORMAT")
check("no FORMAT typed: refused", ok, why_)
ok, why_ = refused_with(dict(req, disk="mmcblk0", id="x", size=1), Ctx(tmp), tree, "not a drive")
check("the eMMC by name: refused by the helper's own look", ok, why_)
ok, why_ = refused_with(dict(req, id="serial:SOMETHING-ELSE"), Ctx(tmp), tree, "not the one shown")
check("a different drive in the slot: refused", ok, why_)
ok, why_ = refused_with(req, Ctx(tmp, mounted=True), tree, "already mounted")
check("a drive already on /mnt/nvme: refused", ok, why_)
ok, why_ = refused_with(dict(req, disk="sdh", id="serial:USB3", size=64 * GB), Ctx(tmp),
                        tree + [USB_MOUNTED], "already mounted")
check("a drive mounted under /media: refused, never unmounted", ok, why_)

# -- 3. no format --------------------------------------------------------------------
print("== use as it is")
ok, why_ = refused_with({"action": "mount", "disk": "nvme0n1", "id": blank["id"],
                         "size": blank["size"]}, Ctx(tmp), tree, "no filesystem")
check("no filesystem: refused, nothing run", ok, why_)
ok, why_ = refused_with({"action": "mount", "disk": "sda", "id": usb["id"],
                         "size": usb["size"]}, Ctx(tmp), tree, "vfat")
check("vfat: refused with the reason, nothing run", ok, why_)
ext = H.describe(USB_EXT)
run, ctx = Fake([EMMC, USB_EXT]), Ctx(tempfile.mkdtemp())
H.setup({"action": "mount", "disk": "sdb", "id": ext["id"], "size": ext["size"]},
        run=run, ctx=ctx, out=io.StringIO())
names = [c[0] for c in run.calls]
check("an ext4 drive is mounted as it is: no wipefs, sfdisk or mkfs",
      not {"wipefs", "sfdisk", "mkfs.ext4"} & set(names)
      and ["mount", "-t", "ext4", "-o", "nosuid,nodev", "/dev/sdb1", "/mnt/nvme"]
      in run.calls, run.calls)
check("...touching only the box's four folders, nothing else on the drive",
      ctx.prepared == APP and not [c for c in run.calls if c[0] in ("chown", "chmod")],
      ctx.prepared)
check("...persisted by its own UUID", "UUID=9f1c-new-uuid  /mnt/nvme  ext4" in
      open(ctx.fstab, encoding="utf-8").read())
check("...nosuid,nodev in fstab too: a setuid file on a drive somebody else wrote does not count",
      "defaults,nosuid,nodev,nofail" in open(ctx.fstab, encoding="utf-8").read())


class _BadUuid(Fake):
    def __call__(self, argv, input=None, timeout=None):
        if argv[0] == "blkid":
            self.calls.append(list(argv))
            return Res(0, "x  /  ext4  defaults  0 0\nUUID=y\n")
        return Fake.__call__(self, argv, input, timeout)


run, ctx = _BadUuid([EMMC, USB_EXT]), Ctx(tempfile.mkdtemp())
try:
    H.setup({"action": "mount", "disk": "sdb", "id": ext["id"], "size": ext["size"]},
            run=run, ctx=ctx, out=io.StringIO())
    bad_ok = False
except RuntimeError as exc:
    bad_ok = "UUID" in str(exc)
check("a UUID that would end the fstab field or line: refused before mount or fstab",
      bad_ok and not [c for c in run.calls if c[0] == "mount"]
      and not os.path.exists(ctx.fstab + ".irivai-bak"), run.calls)
src = open(os.path.join(ROOT, "scripts", "storage_setup.py"), encoding="utf-8", newline="").read()
check("no recursive chown anywhere in the helper",
      "chown -R" not in src.replace("no chown -R", "") and "os.walk" not in src)

# -- 4. repair -----------------------------------------------------------------------
print("== repair (mounted, not writable)")
ctx = Ctx(tmp, mounted=True)
out = H.setup({"action": "repair"}, run=Fake([]), ctx=ctx, out=io.StringIO())
check("a mounted drive gets its four folders, nothing else", ctx.prepared == APP
      and out["dirs"] == [p for p, _u in APP], ctx.prepared)
ok, why_ = refused_with({"action": "repair"}, Ctx(tmp, mounted=True, ro=True), [], "read-only")
check("a read-only mount is reported as such, nothing touched", ok, why_)
check("...and never remounted", "remount" not in src.replace("NOT remounted", ""))
ok, why_ = refused_with({"action": "repair"}, Ctx(tmp), [], "nothing is mounted")
check("nothing mounted: refused", ok, why_)

if os.name == "posix":
    import getpass
    real = H.Ctx()
    base = tempfile.mkdtemp()
    target = os.path.join(base, "dataset_image")
    real.prepare_dir(target, getpass.getuser())
    check("the real prepare_dir makes a 0755 folder", os.path.isdir(target)
          and (os.stat(target).st_mode & 0o777) == 0o755)
    os.symlink("/etc", os.path.join(base, "model"))
    try:
        real.prepare_dir(os.path.join(base, "model"), getpass.getuser())
        check("a symlink where a folder should be is refused", False, "followed")
    except H.Refused:
        check("a symlink where a folder should be is refused", True)
    inner = os.path.join(target, "file.jpg")
    open(inner, "w").close()
    os.chmod(inner, 0o600)
    real.prepare_dir(target, getpass.getuser())
    check("the folder only: a file inside keeps its mode", (os.stat(inner).st_mode & 0o777) == 0o600)

# -- 5. the hub ----------------------------------------------------------------------
print("== the hub: popup, No, repair")
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())
from core import config as config_mod                                     # noqa: E402
from core.services import storage_job as J                                 # noqa: E402
from core.services import storage_setup as S                               # noqa: E402

config_mod.mounted = lambda p: False
cfg = tempfile.mkdtemp()
config_mod.FSTAB = os.path.join(cfg, "fstab-none")
notes = []
roots = ["mmcblk0", "mmcblk0p2"]
run = Fake([EMMC, NVME_NEW, USB_FAT, USB_MOUNTED])
found = S.poll(cfg, run=run, notify=lambda *a, **k: notes.append(a), root_names=roots)
check("an unmounted drive gives the popup; the mounted one does not",
      [d["name"] for d in found] == ["nvme0n1", "sda"] and len(notes) == 2, found)
S.poll(cfg, run=run, notify=lambda *a, **k: notes.append(a), root_names=roots)
check("...one Inbox notice each, not again on the next pass", len(notes) == 2)
check("the prompt asks about the NVMe first", (S.prompt(cfg, found) or {}).get("name") == "nvme0n1")
S.decline("serial:S5NEW", cfg)
check("No on the NVMe: it is not asked again", (S.prompt(cfg, found) or {}).get("name") == "sda")
S.decline("serial:USB1", cfg)
check("No on both: nothing asks", S.prompt(cfg, found) is None)
other = S.scan(Fake([EMMC, disk("nvme0n1", 512 * GB, serial="S5OTHER", tran="nvme")]), roots)
check("a DIFFERENT drive in the same slot asks again",
      (S.prompt(cfg, other) or {}).get("id") == "serial:S5OTHER")
mounted_only = S.scan(Fake([EMMC, STORE, USB_MOUNTED]), roots)
check("a box whose drives are all mounted gets no popup",
      mounted_only == [] and S.prompt(cfg, mounted_only) is None)

try:
    J.start(blank["id"], False, config_dir=cfg, run=Fake([EMMC, NVME_NEW]), root_names=roots,
            popen=lambda *a, **k: None)
    check("the hub refuses no-format on a drive with no filesystem", False, "started")
except ValueError as exc:
    check("the hub refuses no-format on a drive with no filesystem",
          "no filesystem" in str(exc) and J.job()["state"] != "running", str(exc))
try:
    J.start("serial:/dev/sda", True, "FORMAT", config_dir=cfg, run=Fake([EMMC]),
            root_names=roots, popen=lambda *a, **k: None)
    check("an id not in the box's own list is refused", False, "started")
except ValueError as exc:
    check("an id not in the box's own list is refused", "not here" in str(exc), str(exc))

got = {}


class Kept(io.StringIO):
    def close(self):
        got["sent"] = self.getvalue()


class Proc:
    def __init__(self, lines, rc=0, err=""):
        self.stdin = Kept()
        self.stdout = iter(lines)
        self.stderr = io.StringIO(err)
        self._rc = rc

    def wait(self, timeout=None):
        return self._rc


def popen(argv, **kw):
    got["argv"] = argv
    return Proc(["STEP 1 7 Checking the drive\n",
                 'DONE {"mount_point": "/mnt/nvme", "uuid": "u"}\n'])


fin = threading.Event()
J.start(usb["id"], True, "FORMAT", config_dir=cfg, systems=1, user="master",
        run=Fake([EMMC, USB_FAT]), root_names=roots, popen=popen,
        notify=lambda *a, **k: notes.append(a), on_done=lambda j: fin.set())
fin.wait(5)
j = J.job()
check("a format runs the helper through sudo with no arguments",
      got.get("argv") == ["sudo", "-n", "/usr/bin/python3", "-I",
                          "/usr/local/lib/irivai/storage-setup.py"], got.get("argv"))
sent = json.loads(got.get("sent") or "{}")
check("...the request names the drive from the box's own scan, with its identity",
      sent == {"action": "format", "disk": "sda", "id": usb["id"], "size": usb["size"],
               "confirm": "FORMAT"}, sent)
check("...ends done, asks for a reboot, and tells the Inbox",
      j["state"] == "done" and j["reboot"] and notes[-1][1] == "Storage set up", (j, notes[-1:]))

# A mounted drive whose folders are not writable: repaired, never offered.
drive = tempfile.mkdtemp()
config_mod.NVME_MOUNT = drive
config_mod.mounted = lambda p: True
sudo_calls = []


def sudo_ok(argv, input=None):
    sudo_calls.append((argv, input))
    for d in ("cytron", "dataset_image", "dataset_rec", "model"):
        os.makedirs(os.path.join(drive, d), exist_ok=True)
    return Res(0)


warned = []
left = J.keep_writable(cfg, notify=lambda *a, **k: warned.append(a), run=sudo_ok)
check("mounted-not-writable goes to repair: one helper call, folders made, no notice",
      left == "" and len(sudo_calls) == 1 and json.loads(sudo_calls[0][1]) == {"action": "repair"}
      and not warned, (left, sudo_calls, warned))
check("...and the popup never offers it", S.prompt(cfg, found) is None)
left = J.keep_writable(cfg, notify=lambda *a, **k: warned.append(a), run=sudo_ok)
check("...nothing to do once the folders are writable", left == "" and len(sudo_calls) == 1)

# -- 6. the dataset card: only after a repair that failed --------------------------
print("== the dataset card")
from core.services import detector_control as dc                          # noqa: E402

card_root = tempfile.mkdtemp()
# The root is read where dataset_state lives (dataset_capture), not the
# copy detector_control re-exports.
from core.services import dataset_capture                                # noqa: E402
dataset_capture.DATASET_ROOT = card_root
config_mod.NVME_MOUNT = card_root
_ismount = os.path.ismount
os.path.ismount = (lambda p: True if os.path.normpath(p) == os.path.normpath(card_root)
                   else _ismount(p))
J.can_setup = lambda: True


def blocked():
    """dataset_image as a FILE: nothing can be made in it -- the stand-in
    for a root-owned folder, which chmod cannot fake for root or Windows."""
    for d in ("dataset_image", "dataset_rec"):
        p = os.path.join(card_root, d)
        if os.path.isdir(p):
            os.rmdir(p)
    open(os.path.join(card_root, "dataset_image"), "w").close()


def fixed(argv, input=None, timeout=None):
    os.remove(os.path.join(card_root, "dataset_image"))
    os.makedirs(os.path.join(card_root, "dataset_image"))
    return Res(0)


def ro(argv, input=None, timeout=None):
    return Res(2, err="storage-setup: the drive at /mnt/nvme is mounted read-only, "
                      "which usually means the system found errors on it\n")


blocked()
J._sudo = fixed
J._repair["at"] = 0.0           # forced repairs are REPAIR_MIN_S apart
st = dc.dataset_state(force=True)
check("a repair that works: no card at all", st["ok"] is True, st)
blocked()
J._sudo = ro
J._repair["at"] = 0.0
st = dc.dataset_state(force=True)
check("a repair that fails: the card, with the helper's reason (read-only)",
      st["ok"] is False and st["reason"] == "readonly" and "read-only" in st["detail"]
      and "sudo" not in st["detail"], st)
calls = []
J._sudo = lambda argv, input=None, timeout=None: (calls.append(1), ro(argv))[1]
dc.dataset_state()
dc.dataset_state()
check("the page's polls do not run the helper every time (at most once a minute)",
      len(calls) == 0, calls)
J._repair["at"] -= J.REPAIR_MIN_S + 1
dc.dataset_state(force=True)
check("Try again forces one more", len(calls) == 1, calls)
os.path.ismount = _ismount
nos = open(os.path.join(ROOT, "frontend", "src", "components", "NoStorage.jsx"),
           encoding="utf-8").read()
check("the card names no shell command, and has Try again",
      "sudo " not in nos.replace("never types sudo", "") and "setup_nvme.sh" not in nos
      and "Try again" in nos and "/api/detector/dataset/repair" in nos)

# -- 7. install / upgrade / setup_nvme ------------------------------------------------
print("== install, upgrade, setup_nvme")
inst = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
up = open(os.path.join(ROOT, "scripts", "upgrade_steps.sh"), encoding="utf-8").read()
nv = open(os.path.join(ROOT, "scripts", "setup_nvme.sh"), encoding="utf-8").read()
rule = "${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/storage-setup.py"
m = re.search(r"cat > /etc/sudoers.d/irivai-storage.tmp <<SUDOERS\n(.*?)\nSUDOERS", inst, re.S)
check("install.sh: its own no-argument sudoers rule", m and m.group(1).strip() == rule)
check("install.sh: installed root-owned, line endings normalised",
      'install -m 755 "${SRC_DIR}/scripts/storage_setup.py" /usr/local/lib/irivai/storage-setup.py'
      in inst and "chown root:root /usr/local/lib/irivai/storage-setup.py" in inst
      and "/usr/local/lib/irivai/storage-setup.py \\\n" in inst)
# upgrade_steps.sh and every OTA install it from the shared list
# (scripts/root_items.json, smoke_rootsteps.py pins it to install.sh's text).
items = json.load(open(os.path.join(ROOT, "scripts", "root_items.json"), encoding="utf-8"))
check("upgrade_steps.sh and the updates: the same rule, and the helper installed",
      {"src": "scripts/storage_setup.py", "dest": "storage-setup.py"} in items["helpers"]
      and any(s["name"] == "irivai-storage" and s["rule"].replace("{user}", "${SERVICE_USER}") == rule
              for s in items["sudoers"])
      and 'python3 -I "${HERE}/root_steps.py"' in up)
check("setup_nvme.sh: its folder step IS the helper's repair",
      '{"action":"repair"}' in nv and 'SUDO_USER="$SVC_USER" python3 -I "$HELPER"' in nv)
check("the helper reads stdin only and is LF", "sys.argv" not in src and "\r" not in src)
check("no em dash anywhere in the new files", all(
    chr(0x2014) not in open(os.path.join(ROOT, *p.split("/")), encoding="utf-8").read()
    for p in ("scripts/storage_setup.py", "core/services/storage_setup.py",
              "core/services/storage_job.py", "core/routes/storage_setup.py",
              "core/routes/storage_repair.py", "frontend/src/components/NoStorage.jsx",
              "frontend/src/components/NewStoragePrompt.jsx")))

print("FAILED: %d" % bad if bad else "all passed")
sys.exit(1 if bad else 0)
