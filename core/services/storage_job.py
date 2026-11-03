# -*- coding: utf-8 -*-
"""
storage_job.py -- setting up the drive the "New storage detected" popup
asked about (core/services/storage_setup.py finds and remembers drives;
this runs the one root step).

The root step is the helper /usr/local/lib/irivai/storage-setup.py (the
set-hostname pattern: request on stdin, no-argument sudoers, re-checks
everything as root), in a background thread here: "STEP i n text" lines
are the popup's progress, "DONE {json}" the result, the last stderr line
the reason. One job at a time, claimed under the lock before any check.
Without the helper, can_setup() is False and roothelp.py says what is missing.

REPAIR (Tok Aba, same day): a drive mounted at /mnt/nvme whose folders
this account cannot write is never a "new drive": repair() has the helper
make the box's folders (those only), keep_writable() does it from the
hub's pass, and the "Storage is not writable" card is for a failed repair.
"""

import json
import os
import posixpath
import shutil
import subprocess
import threading
import time

from core import config as config_mod
from core.services import roothelp, storage_setup

HELPER = "/usr/local/lib/irivai/storage-setup.py"
#: Exactly the sudoers entry install.sh writes (-I: no caller's path).
HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", HELPER)


def needs_installer():
    return roothelp.missing(HELPER, "irivai-storage", "the box cannot format or mount a drive by itself")


_lock = threading.Lock()
_job = {"state": "idle"}
_can = {"at": 0.0, "ok": False}


def helper_installed():
    return os.path.isfile(HELPER)


def can_setup():
    """Helper there AND sudo would run it without a password (`sudo -n -l`
    runs nothing). Cached a minute: the popup polls."""
    if time.time() - _can["at"] < 60:
        return _can["ok"]
    ok = False
    if helper_installed() and shutil.which("sudo"):
        try:
            ok = subprocess.run(["sudo", "-n", "-l", *HELPER_ARGV[2:]],
                                capture_output=True, text=True,
                                timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _can.update(at=time.time(), ok=ok)
    return ok


def job():
    with _lock:
        return dict(_job)


# ---------------------------------------------------------------------------
# Repair: the drive is mounted, the box's folders on it are not writable
# ---------------------------------------------------------------------------

#: At most this often by itself: the Dataset page polls every few seconds
#: and sudo journals every call. "Try again" forces one, but never more
#: often than REPAIR_MIN_S, and never two at once (Pipi, 2026-10-07).
REPAIR_EVERY_S = 60
REPAIR_MIN_S = 5
_repair = {"at": 0.0, "result": None, "told": False, "busy": False}


def app_dirs_problem(root=None):
    """"" when every one of the box's folders on the mounted drive
    (the helper's APP_DIRS) is a folder this account can write, else the
    first problem in words. No writes: os.access, cheap enough to ask
    on every hub pass."""
    root = root or config_mod.NVME_MOUNT
    for name in storage_setup.shared().APP_DIRS:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            return "%s is missing" % path
        if not os.access(path, os.W_OK | os.X_OK):
            return "%s cannot be written by this service" % path
    return ""


def _sudo(argv, input=None):
    return subprocess.run(argv, input=input, capture_output=True, text=True,
                          timeout=60)


def repair(force=False, run=None):
    """Make the box's folders on /mnt/nvme and hand them to this account,
    through the root helper. {"ok", "reason"}; `reason` is the helper's
    own sentence (a read-only drive says so). Never raises."""
    now = time.time()
    with _lock:
        last, age = _repair["result"], now - _repair["at"]
        if _repair["busy"]:
            return {"ok": False, "reason": "a repair is running already; try again in a moment"}
        if last is not None and age < (REPAIR_MIN_S if force else REPAIR_EVERY_S):
            return dict(last)
        _repair.update(at=now, busy=True)
    if not config_mod.mounted(config_mod.NVME_MOUNT):
        out = {"ok": False, "reason": "no drive is mounted at %s" % config_mod.NVME_MOUNT}
    elif run is None and not can_setup():
        out = {"ok": False, "reason": roothelp.missing(
            HELPER, "irivai-storage", "the box cannot make its folders on the drive writable by itself")}
    else:
        try:
            r = (run or _sudo)(list(HELPER_ARGV), input=json.dumps({"action": "repair"}))
            tail = (r.stderr or "").strip().splitlines()
            out = {"ok": r.returncode == 0,
                   "reason": "" if r.returncode == 0 else
                   (tail[-1] if tail else "the repair stopped without saying why")
                   .replace("storage-setup: ", "", 1)}
        except Exception as exc:                             # noqa: BLE001
            out = {"ok": False, "reason": "the repair did not run: %s" % exc}
    with _lock:
        _repair.update(result=out, busy=False)
    return dict(out)


def _posix(path):
    return posixpath.normpath(str(path).replace("\\", "/"))


def after_repair(root, err, probe, force=False, run=None):
    """The Dataset pages' check found `root` mounted and not writable
    (`err`, its answer): repair, then `probe(repair=False)` once more.
    Only /mnt/nvme is the helper's to repair; any other root (an explicit
    $CYTRON_NVME on a bench) keeps `err`. A failed repair answers with the
    helper's reason, the real one ("mounted read-only"), not the errno."""
    if _posix(root) != _posix(config_mod.NVME_MOUNT):
        return err
    fix = repair(force=force, run=run)
    if fix["ok"]:
        return probe(repair=False)
    return dict(err, detail=fix["reason"] or err["detail"], repaired=False)


def keep_writable(config_dir=None, notify=None, run=None):
    """The hub's pass: a mounted drive whose folders this account cannot
    write is repaired by itself, before anybody meets the "Storage is not
    writable" card. One Inbox notice when the repair genuinely fails, none
    again until it has worked once. Returns the problem left, or ""."""
    if not config_mod.mounted(config_mod.NVME_MOUNT) or not app_dirs_problem():
        return ""
    fix = repair(run=run)
    left = app_dirs_problem() if fix["ok"] else (fix["reason"] or app_dirs_problem())
    if not left:
        _repair["told"] = False
        return ""
    if not _repair["told"]:
        _repair["told"] = True
        if notify is None:
            from core.services import inbox                   # noqa: PLC0415
            notify = inbox.notify_admins
        try:
            notify("storage", "The storage drive is not writable",
                   "The box could not prepare its folders on the drive at %s: "
                   "%s." % (config_mod.NVME_MOUNT, left), config_dir=config_dir,
                   page="settings", tab="retention")
        except Exception:                                    # noqa: BLE001
            pass
    return left


def reboot_needed(action, systems):
    """Format: always (Tok Aba). Mount: only when systems exist -- each
    picks its data dir at start and keeps it, so they reach the drive at
    their next start; a box with no systems yet has nothing to move."""
    return action == "format" or bool(systems)


def start(dev_id, fmt, confirm="", config_dir=None, systems=0, user="",
          run=None, root_names=None, popen=None, notify=None, on_done=None):
    """Begin setting up drive `dev_id` -- looked up in a FRESH scan of our
    own, never a device path from the browser -- in the background. Raises
    ValueError with the admin's sentence when it cannot start. `run`,
    `root_names` and `popen` are for tests."""
    mod = storage_setup.shared()
    action = "format" if fmt else "mount"
    # Claimed first, under the lock: two clicks (two tabs) must not both
    # pass the checks below and start two helpers on one disk.
    with _lock:
        if _job.get("state") == "running":
            raise ValueError("a drive is being set up already")
        before = dict(_job)
        _job.clear()
        _job.update(state="running", action=action, step=0, steps=0,
                    text="Checking the drive", message="", reboot=False,
                    started=time.time())
    try:
        if popen is None and not can_setup():
            raise ValueError(needs_installer())
        if storage_setup._store_has_drive():
            raise ValueError("a drive is already mounted at %s" % config_mod.NVME_MOUNT)
        dev = next((d for d in storage_setup.scan(run, root_names) if d["id"] == dev_id), None)
        if dev is None:
            raise ValueError("that drive is not here any more, or may not be set up")
        if fmt and dev.get("expected"):
            raise ValueError("this is the box's own storage drive, not mounted; "
                             "it can be mounted as it is, never formatted here")
        if fmt and str(confirm or "").strip() != mod.CONFIRM:
            raise ValueError("type %s to confirm erasing the drive" % mod.CONFIRM)
        if not fmt and not dev["usable"]:
            raise ValueError(dev["mount_refusal"])
    except ValueError:
        with _lock:
            _job.clear()
            _job.update(before)
        raise
    req = {"action": action, "disk": dev["name"], "id": dev["id"],
           "size": dev["size"], "confirm": mod.CONFIRM if fmt else ""}
    with _lock:
        _job.update(device=dev)
    threading.Thread(target=_work, args=(req, dev, config_dir, systems, user,
                                         popen, notify, on_done),
                     name="storage-setup", daemon=True).start()
    return job()


def _work(req, dev, config_dir, systems, user, popen, notify, on_done):
    if notify is None:
        from core.services import inbox                       # noqa: PLC0415
        notify = inbox.notify_admins
    popen = popen or subprocess.Popen
    done, err = None, ""
    try:
        proc = popen(list(HELPER_ARGV), stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc.stdin.write(json.dumps(req))
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("STEP "):
                parts = line.split(" ", 3)
                with _lock:
                    _job.update(step=int(parts[1]), steps=int(parts[2]),
                                text=parts[3] if len(parts) > 3 else "")
            elif line.startswith("DONE "):
                done = json.loads(line[5:])
        tail = (proc.stderr.read() or "").strip().splitlines()
        rc = proc.wait(timeout=60)
        if rc != 0 or done is None:
            err = (tail[-1] if tail else "the helper stopped without saying why")
            err = err.replace("storage-setup: ", "", 1)
    except Exception as exc:                                 # noqa: BLE001
        err = "the setup did not run: %s" % exc
    what = "%s, %s" % (dev["model"], dev["size_text"])
    if err:
        with _lock:
            _job.update(state="failed", message=err, finished=time.time())
        title, body = ("Storage setup failed",
                       "Setting up %s failed: %s" % (what, err))
    else:
        reboot = reboot_needed(req["action"], systems)
        with _lock:
            _job.update(state="done", result=done, reboot=reboot,
                        finished=time.time(),
                        message=("%s is mounted at %s and will be used for "
                                 "storing files." % (what, done["mount_point"])))
        title = "Storage set up"
        body = ("%s was %s and mounted at %s for storing files%s. Restart the "
                "box so every system saves to it%s." % (
                    what, "formatted (all data on it erased)"
                    if req["action"] == "format" else "mounted as it was",
                    done["mount_point"], (" by " + user) if user else "",
                    "" if reboot else " (not needed now: no systems yet)"))
        # The drive is expected from now on (fstab names it); the alarm
        # and the retention page look again at once instead of in a minute.
        try:
            from core.services import storage_watch          # noqa: PLC0415
            storage_watch._last.update(at=0.0, state=None)
        except Exception:                                    # noqa: BLE001
            pass
    try:
        notify("storage", title, body, config_dir=config_dir,
               page="settings", tab="retention")
    except Exception:                                        # noqa: BLE001
        pass
    with _lock:
        storage_setup._cache.update(at=0.0)
    if on_done:
        try:
            on_done(job())
        except Exception:                                    # noqa: BLE001
            pass
