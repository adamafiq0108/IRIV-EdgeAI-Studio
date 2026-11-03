#!/usr/bin/python3 -I
# -*- coding: utf-8 -*-
"""
storage_setup.py -- put a newly fitted drive to work as the box's storage,
and keep the box's own folders on it writable. Run as root through sudo by
the hub and the masters (core/services/storage_job.py), and by
scripts/setup_nvme.sh for its folder step,
installed root-owned at /usr/local/lib/irivai/storage-setup.py by
scripts/install.sh and scripts/upgrade_steps.sh. The request arrives on stdin
as one JSON object; nothing on argv, so the sudoers rule names this file with
no arguments (the mqtt-apply / set-hostname pattern).

Tok Aba 2026-10-07: a box that had no NVMe and later gets one (or a USB/SATA
disk) says so in a popup, and the admin can format it or mount what is on it
from the dashboard. "The data is on the NVMe" was only ever reachable with an
SSH session and scripts/setup_nvme.sh until now.

THE TRIGGER IS MOUNT STATE (Tok Aba, same day):
  * a drive with anything of it mounted -- at /mnt/nvme, by fstab, by hand,
    by the desktop under /media -- is READY, never offered, never touched;
  * a drive with nothing of it mounted is offered: format, or mount what is
    on it;
  * /mnt/nvme mounted but the box's folders on it not writable is REPAIRED
    (action "repair"): the folders below are created and handed to the
    service account, and nothing else on the drive is touched. A drive
    mounted read-only is reported as such and NOT remounted: that is the
    kernel protecting a filesystem with errors, and no script of ours has
    ever overridden it.

WHERE IT GOES. The drive is mounted at /mnt/nvme -- core/config.py
NVME_MOUNT, the app's NVME_ROOT -- and nowhere else, because that mount point
IS the setting: every system's app puts its data under /mnt/nvme/cytron/
<slug>/data the next time it starts (systems/*/app/config.py data_dir),
storage_watch expects the drive once fstab lists it, and the retention caps
follow from where that data then lives. So this writes no app setting at
all; it makes the drive be where the box already looks.

THE GATE. The hub's list is advice; this is the gate. The hub imports the
pure half of this file (eligible() and friends) from the release tree to
draw its popup, and this root-owned copy asks lsblk again and decides again
right before anything is touched:
  * a drive is offered only when it is a whole nvme* or sd* disk that holds
    none of /, /boot, /boot/firmware (nor the device / is on by st_dev), is
    not mmcblk / zram / loop / ram, has no LVM, RAID or encryption on it, and
    has nothing of it mounted anywhere;
  * the request names the drive by kernel name AND its identity (WWN, serial
    or filesystem UUID) AND its size: a different drive in the same slot
    between the popup and the click is refused, not formatted;
  * format needs confirm == "FORMAT", typed by the admin;
  * /mnt/nvme must not already have a drive mounted on it.

WHAT IT WRITES. The disk itself (format only: one GPT partition, ext4,
label IRIV-DATA), one /etc/fstab line by UUID with nofail (any active line
for /mnt/nvme or that UUID is commented out, the old file kept as
/etc/fstab.irivai-bak), and the box's folders on the drive (APP_DIRS),
created when missing and handed to the service account -- each folder
itself, never what is in it (no chown -R: a drive used "as it is" keeps
every file as it was). setup_nvme.sh's folder step is this script's
"repair", so the two cannot drift. The PostgreSQL move setup_nvme.sh also
does is NOT done here: moving a live database from a popup is a different
decision.

THE SERVICE ACCOUNT is SUDO_USER: sudo sets it from the real caller (and
env_reset drops anything the caller set), so it is whoever the hub or the
master actually runs as -- cytron on an appliance, pi on a bench box --
without a name written down anywhere. setup_nvme.sh sets it to its own
SERVICE_USER when it calls this directly as root.

Output: "STEP i n text" lines on stdout as it goes (the popup's progress),
then "DONE {json}"; a refusal or failure is one "storage-setup: ..." line on
stderr and exit 2.
"""
import json
import os
import posixpath
import sys
import time

#: Where the drive goes: core/config.py NVME_MOUNT, the app's NVME_ROOT.
MOUNT_POINT = "/mnt/nvme"
FSTAB = "/etc/fstab"
LABEL = "IRIV-DATA"
#: What a typed confirmation must say before a disk is erased.
CONFIRM = "FORMAT"
#: Filesystems the box can keep its files on: Linux ones, with file
#: ownership. vfat / exfat / ntfs cannot give the service account its own
#: folders, so a drive with one of those is offered a format, not a mount.
SUPPORTED_FS = ("ext4", "ext3", "ext2", "xfs", "btrfs")
#: Mount points that make a disk the system's own. Never offered.
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/media/root-ro", "/usr",
                 "/var", "/home", "[SWAP]")
#: The box's own folders on the drive, from the code that writes there:
#:   cytron         every system's data and the store (the app's NVME_DIR,
#:                  core/config.py STORE_CANDIDATES), as setup_nvme.sh makes it
#:   dataset_image  training photos (detector_control DATASET_IMG_DIR), ditto
#:   dataset_rec    training clips (DATASET_REC_DIR), ditto
#:   model          custom models dropped on the drive (config._nvme_model_dir
#:                  creates it, which a root-owned mount refuses)
APP_DIRS = ("cytron", "dataset_image", "dataset_rec", "model")
#: Whole-disk kernel names that can be offered at all.
OFFER_PREFIXES = ("nvme", "sd")
#: Never offered, whatever else they say: the boot eMMC/SD, swap in RAM,
#: loop images, RAM disks.
NEVER_PREFIXES = ("mmcblk", "zram", "loop", "ram")
#: Smaller than this is a card reader with no card or a firmware gadget.
MIN_BYTES = 1 << 30
LSBLK_COLS = ("NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RM,FSTYPE,LABEL,"
              "UUID,MOUNTPOINTS")
#: How the drive is mounted, now and in fstab. nosuid,nodev (Pipi
#: 2026-10-07): "Use as it is" mounts a filesystem somebody else wrote, at
#: the path the box runs code from; a setuid-root file or a device node on
#: it must not count. Not noexec: the store's systems run from here.
MOUNT_OPTS = "nosuid,nodev"
#: What a filesystem UUID may contain before it goes into /etc/fstab:
#: nothing that could end the field or the line.
UUID_CHARS = frozenset("0123456789abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ-")


# ---------------------------------------------------------------------------
# The pure half: shared with the hub (it imports this file to draw the popup)
# ---------------------------------------------------------------------------

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _mounts(node):
    """A node's mount points as a list: lsblk 2.37+ says MOUNTPOINTS (a list
    with nulls), older ones MOUNTPOINT (one string or null)."""
    if "mountpoints" in node:
        return [m for m in (node.get("mountpoints") or []) if m]
    m = node.get("mountpoint")
    return [m] if m else []


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def size_text(n):
    n = float(_int(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1000.0
    return "%d B" % n


def device_key(disk):
    """The identity a decision is remembered by and a request is checked
    against: WWN, else serial, else the first filesystem UUID on it, else
    model and size. Stable across reboots and slots, unlike sda / nvme0n1."""
    wwn = str(disk.get("wwn") or "").strip()
    if wwn:
        return "wwn:" + wwn
    serial = str(disk.get("serial") or "").strip()
    if serial:
        return "serial:" + serial
    for node in walk(disk):
        if node.get("uuid"):
            return "uuid:" + str(node["uuid"])
    return "dev:%s:%d" % (str(disk.get("model") or "").strip() or "unknown",
                          _int(disk.get("size")))


def _under_store(mp, mount_point):
    return mp == mount_point or mp.startswith(mount_point.rstrip("/") + "/")


def exclusion(disk, root_names=(), mount_point=MOUNT_POINT):
    """Why this lsblk top-level node may NOT be offered, or None. Order is
    the order of certainty: what it is, then what it holds, then its use."""
    name = str(disk.get("name") or "")
    if disk.get("type") != "disk":
        return "not a whole disk"
    if name.startswith(NEVER_PREFIXES):
        return "the built-in storage or a RAM/loop device"
    if not name.startswith(OFFER_PREFIXES):
        return "not an NVMe, USB or SATA disk"
    nodes = list(walk(disk))
    names = {str(n.get("name") or "") for n in nodes}
    mps = [m for n in nodes for m in _mounts(n)]
    if names & set(root_names) or any(m in SYSTEM_MOUNTS for m in mps):
        return "the disk the system runs from"
    if any(_under_store(m, mount_point) for m in mps):
        return "already the box's storage"
    if any(n.get("type") not in ("disk", "part") for n in nodes):
        return "in use by LVM, RAID or encryption"
    if mps:
        return "already mounted: ready to use as it is"
    if _int(disk.get("size")) < MIN_BYTES:
        return "too small or no media"
    return None


def describe(disk):
    """What the popup shows and the helper acts on, for one offerable disk."""
    parts = [c for c in (disk.get("children") or []) if c.get("type") == "part"]
    fs_nodes = parts + ([disk] if disk.get("fstype") else [])
    out_parts = [{"name": p.get("name"), "path": p.get("path") or "/dev/" + str(p.get("name")),
                  "size": _int(p.get("size")), "size_text": size_text(p.get("size")),
                  "fstype": p.get("fstype") or "", "label": p.get("label") or "",
                  "uuid": p.get("uuid") or "", "mounts": _mounts(p)}
                 for p in fs_nodes]
    usable = [p for p in out_parts if p["fstype"] in SUPPORTED_FS and p["uuid"]]
    usable.sort(key=lambda p: -p["size"])
    refusal = ""
    if not usable:
        found = sorted({p["fstype"] for p in out_parts if p["fstype"]})
        if found:
            refusal = ("This drive has a %s filesystem, which cannot hold the "
                       "box's files (it has no Linux file ownership). Format "
                       "it to use it." % " / ".join(found))
        else:
            refusal = ("This drive has no filesystem the box can use. Format "
                       "it to use it.")
    tran = str(disk.get("tran") or "")
    name = str(disk.get("name") or "")
    kind = ("NVMe" if name.startswith("nvme") or tran == "nvme"
            else "USB" if tran == "usb" else "SATA" if tran in ("sata", "ata")
            else "disk")
    return {"id": device_key(disk), "name": name,
            "path": disk.get("path") or "/dev/" + name,
            "model": str(disk.get("model") or "").strip() or "Unknown drive",
            "serial": str(disk.get("serial") or "").strip(),
            "size": _int(disk.get("size")), "size_text": size_text(disk.get("size")),
            "kind": kind, "removable": bool(_int(disk.get("rm")) or disk.get("rm") is True),
            "partitions": out_parts,
            "usable": usable[0] if usable else None, "mount_refusal": refusal}


def eligible(tree, root_names=(), mount_point=MOUNT_POINT):
    """(offerable devices, [(name, why not)]) from lsblk's blockdevices."""
    offered, refused = [], []
    for disk in tree or []:
        why = exclusion(disk, root_names, mount_point)
        if why:
            refused.append((str(disk.get("name") or ""), why))
        else:
            offered.append(describe(disk))
    return offered, refused


def lsblk(run):
    """lsblk's tree, bytes for sizes. MOUNTPOINTS first, MOUNTPOINT for an
    lsblk older than 2.37."""
    for cols in (LSBLK_COLS, LSBLK_COLS.replace("MOUNTPOINTS", "MOUNTPOINT")):
        r = run(["lsblk", "-J", "-b", "-o", cols])
        if r.returncode == 0:
            return (json.loads(r.stdout or "{}").get("blockdevices")) or []
    raise RuntimeError("lsblk did not answer")


def _disk_of_devnum(devnum, sys_root="/sys"):
    """The kernel names (partition and its disk) behind an st_dev."""
    try:
        node = os.path.realpath(os.path.join(
            sys_root, "dev", "block", "%d:%d" % (os.major(devnum), os.minor(devnum))))
    except (AttributeError, OSError, ValueError):
        return []
    if not os.path.isdir(node):
        return []
    out = [os.path.basename(node)]
    if os.path.exists(os.path.join(node, "partition")):
        out.append(os.path.basename(os.path.dirname(node)))
    return out


def root_names(sys_root="/sys", mounts="/proc/self/mounts"):
    """Kernel names the system runs from, asked of the kernel rather than of
    lsblk: the device behind / and /boot by st_dev, and the source of every
    system mount in /proc. A root lsblk does not show as "/" (an overlay
    root, a bind) is still found here."""
    names = set()
    for path in ("/", "/boot", "/boot/firmware"):
        try:
            names.update(_disk_of_devnum(os.stat(path).st_dev, sys_root))
        except OSError:
            pass
    try:
        with open(mounts, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] in SYSTEM_MOUNTS \
                        and parts[0].startswith("/dev/"):
                    names.add(os.path.basename(os.path.realpath(parts[0])))
    except OSError:
        pass
    return sorted(names)


def fstab_uuids(text):
    """Filesystem UUIDs named on ACTIVE /etc/fstab lines (UUID=... or
    /dev/disk/by-uuid/...); a commented-out line names nothing."""
    out = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0].startswith("#"):
            continue
        src = parts[0]
        for pre in ("UUID=", "/dev/disk/by-uuid/"):
            if src.startswith(pre) and len(src) > len(pre):
                out.add(src[len(pre):].strip('"').lower())
    return out


#: Tok Aba 2026-10-07: a drive that already holds another box's IRIV
#: store is not "used as it is" here -- two boxes' systems, layouts and
#: workflows would meet in one store (core/config.py store_dir refuses to
#: merge them and warns for ever). Format it, or it is the box's own
#: drive (fstab names it), which is the recovery and stays allowed.
FOREIGN = ("This drive already holds IRIV software from another box. "
           "Format it to use it here.")
#: What marks it: the store directory under the box's own folder.
FOREIGN_MARK = ("cytron", "store")
#: How it is looked at: read-only, nothing on it may run or be a device.
PROBE_OPTS = "ro,nosuid,nodev,noexec"

#: The label a drive fstab expects, shown where it is offered.
EXPECTED_LABEL = "This box's storage drive, not mounted"


def mark_expected(devices, fstab):
    """Flag each offered drive whose filesystem an active fstab line names:
    the box's own storage drive that did not mount (at boot, a loose cable).
    It may be MOUNTED again from the popup -- that is the recovery -- and
    never formatted (Pipi, 2026-10-07: formatting it erased the box's data)."""
    listed = fstab_uuids(fstab)
    for d in devices:
        d["expected"] = any(p["uuid"] and p["uuid"].lower() in listed
                            for p in d["partitions"])
        d["expected_label"] = EXPECTED_LABEL if d["expected"] else ""
    return devices


def fstab_text(old, uuid, fstype, mount_point=MOUNT_POINT, stamp=""):
    """The new /etc/fstab: every active line for `mount_point` or for this
    UUID commented out (kept, so the change is readable and reversible), and
    one line by UUID with nofail -- a drive missing at boot must never stop
    the box from booting -- and a short device timeout for the same reason."""
    out = []
    for line in old.splitlines():
        parts = line.split()
        active = parts and not parts[0].startswith("#")
        if active and len(parts) >= 2 and (
                parts[1] == mount_point or parts[0] in ("UUID=" + uuid, "/dev/disk/by-uuid/" + uuid)):
            out.append("# irivai storage-setup %s replaced: %s" % (stamp, line))
        else:
            out.append(line)
    out.append("UUID=%s  %s  %s  defaults,%s,nofail,x-systemd.device-timeout=10s  0  2"
               % (uuid, mount_point, fstype, MOUNT_OPTS))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# The root half
# ---------------------------------------------------------------------------

class Refused(Exception):
    """One sentence for the admin; nothing was changed when it is raised
    before the first STEP that writes."""


#: One setup or repair at a time on the box, whoever asks (the hub, a
#: master, setup_nvme.sh): two helpers on one disk is the case to prevent.
LOCK_PATH = "/run/irivai-storage-setup.lock"
BUSY = ("another storage setup or repair is running on this box; try again "
        "when it has finished")


class Ctx:
    """The machine, as the steps see it. A test hands in its own."""
    fstab = FSTAB
    mount_point = MOUNT_POINT
    sys_root = "/sys"
    proc_mounts = "/proc/self/mounts"
    lock_path = LOCK_PATH

    def lock(self):
        """Take the box-wide lock without waiting; Refused(BUSY) when it is
        held. Returns what unlock() releases."""
        import fcntl                                          # noqa: PLC0415
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise Refused(BUSY) from exc
        return fd

    def unlock(self, held):
        os.close(held)              # closing drops the flock

    #: Where the read-only look mounts the drive: /run is root's tmpfs.
    probe_base = "/run"

    def holds_iriv(self, run, part, fstype):
        """Whether `part` already holds an IRIV store (FOREIGN_MARK): it is
        mounted READ-ONLY (PROBE_OPTS) on a fresh folder under /run, looked
        at, and unmounted again. Nothing on the drive is written."""
        import tempfile                                       # noqa: PLC0415
        where = tempfile.mkdtemp(prefix="irivai-probe-", dir=self.probe_base)
        try:
            _must(run, ["mount", "-t", fstype, "-o", PROBE_OPTS, part, where],
                  what="the read-only look at the drive")
            try:
                return os.path.isdir(os.path.join(where, *FOREIGN_MARK))
            finally:
                run(["umount", where])
        finally:
            try:
                os.rmdir(where)
            except OSError:
                pass

    def euid(self):
        return os.geteuid()

    def service_user(self):
        """Who asked: sudo sets SUDO_USER from the real caller (env_reset
        drops anything the caller set). The service account owns the
        folders; root asking for itself is refused."""
        import pwd                                            # noqa: PLC0415
        user = os.environ.get("SUDO_USER") or ""
        if not user or user == "root":
            raise Refused("run through sudo by the service account")
        try:
            pwd.getpwnam(user)
        except KeyError as exc:
            raise Refused("unknown account %s" % user) from exc
        return user

    def is_mounted(self, path):
        return os.path.ismount(path)

    def root_names(self):
        return root_names(self.sys_root, self.proc_mounts)

    def makedirs(self, path):
        os.makedirs(path, exist_ok=True)

    def mount_ro(self, path):
        """Whether the filesystem at `path` is mounted read-only, from
        /proc (the last line for the point wins, as the kernel stacks)."""
        ro = False
        try:
            with open(self.proc_mounts, encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 4 and parts[1] == path:
                        ro = "ro" in parts[3].split(",")
        except OSError:
            pass
        return ro

    def prepare_dir(self, path, user):
        """One of the box's folders: made 0755 when missing, else given
        u+rwx; handed to `user`. The folder only, never its contents, and
        never through a link: a symlink planted where a folder should be
        would otherwise hand root's chown to whatever it points at.

        Changed through ONE descriptor opened O_NOFOLLOW | O_DIRECTORY, not
        by path: a check by path then a chown/chmod by path leaves a window
        in which the folder can be swapped for a link."""
        import pwd                                            # noqa: PLC0415
        import stat                                           # noqa: PLC0415
        pw = pwd.getpwnam(user)
        try:
            os.mkdir(path, 0o755)
        except FileExistsError:
            pass
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise Refused("%s is not a plain folder; it was left alone" % path) from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise Refused("%s is not a plain folder; it was left alone" % path)
            os.fchown(fd, pw.pw_uid, pw.pw_gid)
            os.fchmod(fd, stat.S_IMODE(st.st_mode) | 0o700)
        finally:
            os.close(fd)

    def read(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def write_fstab(self, text):
        """Atomically, with the old file kept beside it."""
        old = self.read(self.fstab)
        with open(self.fstab + ".irivai-bak", "w", encoding="utf-8") as fh:
            fh.write(old)
        tmp = self.fstab + ".irivai-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.fstab)

    def sleep(self, s):
        time.sleep(s)


def _run(argv, input=None, timeout=1800):
    import subprocess                                         # noqa: PLC0415
    return subprocess.run(argv, input=input, capture_output=True, text=True,
                          timeout=timeout)


def _must(run, argv, input=None, what=""):
    r = run(argv, input=input)
    if r.returncode != 0:
        tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["no reason given"])[-1]
        raise RuntimeError("%s failed: %s" % (what or argv[0], tail))
    return r


def _say(out, i, n, text):
    out.write("STEP %d %d %s\n" % (i, n, text))
    out.flush()


def prepare_dirs(ctx, user):
    """APP_DIRS on the drive at ctx.mount_point, for `user`. Returns them."""
    done = []
    for name in APP_DIRS:
        path = posixpath.join(ctx.mount_point, name)
        ctx.prepare_dir(path, user)
        done.append(path)
    return done


def repair(ctx, out=sys.stdout):
    """The drive is mounted and the box cannot write its folders: make them
    and hand them over. Refused, with the reason, when there is no drive
    at the mount point or it is mounted read-only."""
    mp = ctx.mount_point
    if not ctx.is_mounted(mp):
        raise Refused("nothing is mounted at %s" % mp)
    if ctx.mount_ro(mp):
        raise Refused("the drive at %s is mounted read-only, which usually "
                      "means the system found errors on it. Restart the box; "
                      "if it stays read-only the drive needs checking or "
                      "replacing" % mp)
    _say(out, 1, 1, "Preparing the folders")
    return {"action": "repair", "mount_point": mp,
            "dirs": prepare_dirs(ctx, ctx.service_user())}


def _find(run, ctx, req):
    """The requested drive from a FRESH look, checked against its identity."""
    devices, refused = eligible(lsblk(run), ctx.root_names(), ctx.mount_point)
    name = str(req.get("disk") or "")
    dev = next((d for d in devices if d["name"] == name), None)
    if dev is None:
        why = dict(refused).get(name)
        raise Refused("%s is not a drive this box may set up%s" % (
            name or "that", (" (%s)" % why) if why else ""))
    if dev["id"] != str(req.get("id") or "") or dev["size"] != _int(req.get("size")):
        raise Refused("the drive in that slot is not the one shown; open the "
                      "storage setup again")
    return dev


def setup(req, run=_run, ctx=None, out=sys.stdout):
    """Format, mount or repair, as asked, under the box-wide lock (a second
    caller is refused at once, BUSY). Returns the DONE payload; raises
    Refused (nothing changed) or RuntimeError (a step failed, said which)."""
    ctx = ctx or Ctx()
    if ctx.euid() != 0:
        raise Refused("must run as root")
    held = ctx.lock()
    try:
        return _setup(req, run, ctx, out)
    finally:
        ctx.unlock(held)


def inspect(run, ctx, req):
    """What the popup must know before offering "use it as it is": is this
    drive another box's IRIV drive? Only a usable, not-expected drive is
    looked at (read-only); nothing is written either way."""
    dev = _find(run, ctx, req)
    mark_expected([dev], ctx.read(ctx.fstab))
    foreign = bool(dev["usable"]) and not dev["expected"] and ctx.holds_iriv(
        run, dev["usable"]["path"], dev["usable"]["fstype"])
    return {"action": "inspect", "foreign": foreign,
            "message": FOREIGN if foreign else ""}


def _setup(req, run, ctx, out):
    action = req.get("action")
    if action == "repair":
        return repair(ctx, out)
    if action == "inspect":
        return inspect(run, ctx, req)
    user = ctx.service_user()
    if action not in ("format", "mount"):
        raise Refused("action must be format, mount, repair or inspect")
    if action == "format" and str(req.get("confirm") or "") != CONFIRM:
        raise Refused('type %s to confirm erasing the drive' % CONFIRM)
    mp = ctx.mount_point
    if ctx.is_mounted(mp):
        raise Refused("another drive is already mounted at %s" % mp)
    dev = _find(run, ctx, req)
    mark_expected([dev], ctx.read(ctx.fstab))
    if action == "format" and dev["expected"]:
        raise Refused("this is the box's own storage drive (listed in "
                      "/etc/fstab); it did not mount, and it is not formatted "
                      "from here. Use it as it is (Mount) to bring it back")
    if action == "mount" and not dev["usable"]:
        raise Refused(dev["mount_refusal"])
    if action == "mount" and not dev["expected"] and ctx.holds_iriv(
            run, dev["usable"]["path"], dev["usable"]["fstype"]):
        raise Refused(FOREIGN)

    steps = (["Checking the drive", "Erasing what is on it",
              "Creating the partition", "Creating the filesystem", "Mounting it",
              "Saving it for every boot", "Preparing the folders"]
             if action == "format" else
             ["Checking the drive", "Mounting it", "Saving it for every boot",
              "Preparing the folders"])
    n = len(steps)
    step = iter(range(1, n + 1))
    _say(out, next(step), n, steps[0])

    if action == "format":
        _say(out, next(step), n, steps[1])
        for p in dev["partitions"]:
            if p["path"] != dev["path"]:
                _must(run, ["wipefs", "-a", p["path"]], what="wipefs")
        _must(run, ["wipefs", "-a", dev["path"]], what="wipefs")
        _say(out, next(step), n, steps[2])
        _must(run, ["sfdisk", "--wipe", "always", "--wipe-partitions", "always",
                    dev["path"]], input="label: gpt\n,,L\n", what="sfdisk")
        run(["udevadm", "settle"])
        part = None
        for _ in range(10):
            fresh = next((d for d in lsblk(run) if d.get("name") == dev["name"]), {})
            kids = [c for c in (fresh.get("children") or []) if c.get("type") == "part"]
            if len(kids) == 1:
                part = kids[0].get("path") or "/dev/" + str(kids[0].get("name"))
                break
            ctx.sleep(0.5)
        if not part:
            raise RuntimeError("the new partition did not appear")
        _say(out, next(step), n, steps[3])
        _must(run, ["mkfs.ext4", "-F", "-L", LABEL, part], what="mkfs.ext4")
        run(["udevadm", "settle"])
        fstype = "ext4"
    else:
        part = dev["usable"]["path"]
        fstype = dev["usable"]["fstype"]
    uuid = _must(run, ["blkid", "-s", "UUID", "-o", "value", part],
                 what="blkid").stdout.strip()
    if not uuid:
        raise RuntimeError("the filesystem has no UUID")
    if len(uuid) > 64 or not set(uuid) <= UUID_CHARS:
        raise RuntimeError("the filesystem's UUID is not one fstab can name")

    _say(out, next(step), n, steps[-3])
    ctx.makedirs(mp)
    _must(run, ["mount", "-t", fstype, "-o", MOUNT_OPTS, part, mp], what="mount")
    try:
        _say(out, next(step), n, steps[-2])
        ctx.write_fstab(fstab_text(ctx.read(ctx.fstab), uuid, fstype, mp,
                                   time.strftime("%Y-%m-%d")))
        run(["systemctl", "daemon-reload"])
        _say(out, next(step), n, steps[-1])
        prepare_dirs(ctx, user)
    except Exception:
        run(["umount", mp])            # never leave it half set up and mounted
        raise
    return {"action": action, "disk": dev["name"], "partition": part,
            "uuid": uuid, "fstype": fstype, "mount_point": mp,
            "label": LABEL if action == "format" else dev["usable"]["label"]}


def main():
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    try:
        req = json.loads(sys.stdin.read(4096) or "{}")
        if not isinstance(req, dict):
            raise ValueError("not an object")
    except ValueError:
        print("storage-setup: the request is not JSON", file=sys.stderr)
        return 2
    try:
        done = setup(req)
    except (Refused, RuntimeError, OSError) as exc:
        print("storage-setup: %s" % exc, file=sys.stderr)
        return 2
    print("DONE " + json.dumps(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
