# -*- coding: utf-8 -*-
"""
update_offline.py -- update a box from a file instead of from GitHub.

For a box with no internet, or no update token: an admin uploads ONE offline
update file (core/services/offline_file.py says what one is and checks it)
and installs it through the same applier, with the same preflight, flip,
health gate and rollback as an online update. Only the download is skipped:
the job says source = "local" and names the directory the three parts wait
in.

The upload is streamed to disk (never held in memory) into a private
directory, 0700, under the update dir on the store, capped at MAX_UPLOAD and
refused up front when the declared size is over the cap or the store has no
room for it. The filename the browser sends is never read. Any refusal
removes the whole directory before a job, a status or a release is written.

Version rules (Tok Aba, 2026-10-05): older is refused (Roll back is the way
back), the same version is allowed only after a confirm, newer installs. A
developer file installs only on a developer box and a customer file only on
a customer box: the signed manifest's `app` decides.

One file waits at a time, for at most EXPIRE_S. It is removed on install (by
the applier, when its job ends), on cancel, on expiry (the update loop
sweeps every tick) and at hub start.

The upload slot (Pipi F2-F4, 2026-10-05): an upload with no Content-Length
is budgeted as MAX_UPLOAD and the store is re-checked every ROOM_EVERY bytes
while it streams; the slot is held for at most DEADLINE_S and a chunk may
take at most IDLE_S to arrive, so a dead link cannot keep it; Cancel marks
an upload in flight, which stops at its next chunk and frees the slot.
Install holds the slot across its hand-off, and nothing clears ready/ while
the meta says installing and the job it started has not ended.
"""

import json
import os
import secrets
import shutil
import threading
import time

from core.services.offline_file import (BUNDLE, MAX_UPLOAD, OfflineError,  # noqa: F401
                                        verify_file)

#: How long an uploaded file waits for its Install press.
EXPIRE_S = 3600
#: The whole upload, and one chunk of it: a laptop on the box's own LAN
#: sends a 60 MB file in seconds; 20 minutes is a slow Wi-Fi link, not a
#: dead one.
DEADLINE_S = 20 * 60
IDLE_S = 60
#: Free space is looked at again this often while the body streams in.
ROOM_EVERY = 64 << 20

_busy = threading.Lock()
_cancelled = threading.Event()


# ---------------------------------------------------------------------------
# The one waiting file, on this box
# ---------------------------------------------------------------------------

def _update():
    from core.services import update                       # noqa: PLC0415
    return update


def offline_dir():
    """<update dir>/offline, 0700: only the service account reads it."""
    path = _update()._p("offline")
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def ready_dir():
    return os.path.join(offline_dir(), "ready")


def _meta():
    try:
        with open(os.path.join(ready_dir(), "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _clear():
    path = offline_dir()
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def _handing_off(upd, meta):
    """True while a file is the applier's: Install marked it and the job it
    started has not ended. job_running() alone is False in the "starting"
    window (status written with pid 0 until the applier writes its own), and
    a sweep or a second upload in that window would wipe ready/ under an
    Install already pressed. install() ends the status itself when the unit
    fails to start; a hand-off lost any other way is given up on after
    EXPIRE_S, so nothing can wedge the slot for good."""
    if not meta.get("installing"):
        return False
    st = upd.status()
    return st["phase"] not in upd.DONE_PHASES and time.time() - st["started"] <= EXPIRE_S


def sweep():
    """Remove a waiting file that expired, a half upload, or a file a job
    already took. Never while an upload is being received, a job runs or
    Install is handing a file over."""
    if not _busy.acquire(blocking=False):
        return
    try:
        upd = _update()
        if upd.job_running():
            return
        meta = _meta()
        if meta.get("installing"):
            # The applier removes it when its job ends; only a job that ended
            # without doing so, or a hand-off that never arrived, leaves it.
            if not _handing_off(upd, meta):
                _clear()
        elif not meta or meta.get("boot_id") != upd._boot_id() \
                or time.time() - float(meta.get("at") or 0) > EXPIRE_S:
            _clear()
    finally:
        _busy.release()


def check_room(length, written=0):
    """The store must hold the rest of the upload plus a copy of it (the
    bundle is copied out of the archive) with 64 MB to spare: it is the
    partition recordings go to. `length` is the declared size, or MAX_UPLOAD
    when the request declared none."""
    st = os.statvfs(offline_dir()) if hasattr(os, "statvfs") else None
    if st and st.f_bavail * st.f_frsize < 2 * length - written + (64 << 20):
        raise OfflineError("There is not enough free space on this box for "
                           "that file.")


def begin(length=0):
    """Claim the upload slot and return the path to stream into. Refuses a
    declared size over the cap, or more than the store has room for, before
    a byte is read."""
    if length and length > MAX_UPLOAD:
        raise OfflineError("That file is too large to be an update file.")
    upd = _update()
    if upd.job_running() or _handing_off(upd, _meta()):
        raise OfflineError("An update is already running. Try again when it "
                           "has finished.")
    if not _busy.acquire(blocking=False):
        raise OfflineError("Another update file is being uploaded right now.")
    try:
        _cancelled.clear()
        _clear()
        check_room(length or MAX_UPLOAD)
    except BaseException:
        _busy.release()
        raise
    return os.path.join(offline_dir(), "upload.part")


def received(size, added, length, started):
    """Called by the route after every chunk (`added` bytes, `size` in all):
    the running total against the cap, the deadline, a Cancel pressed
    meanwhile, and the store's room each time the total crosses a ROOM_EVERY
    line. `started` is the time.monotonic() the upload began at."""
    if size > MAX_UPLOAD:
        raise OfflineError("That file is too large to be an update file.")
    if _cancelled.is_set():
        raise OfflineError("The upload was cancelled.")
    if time.monotonic() - started > DEADLINE_S:
        raise OfflineError("The upload took too long, so it was stopped. Try "
                           "again on a faster connection.")
    if size // ROOM_EVERY != (size - added) // ROOM_EVERY:
        check_room(length or MAX_UPLOAD, size)


def open_part(path):
    """The upload's own file, 0600 from the first byte."""
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    return open(os.open(path, flags, 0o600), "wb")


def end():
    """Release the upload slot (always, after begin)."""
    try:
        _busy.release()
    except RuntimeError:
        pass


def discard():
    """Cancel: remove whatever is waiting (not a file a job is installing).
    An upload in flight is told to stop; it frees the slot at its next chunk
    (or at IDLE_S, if no chunk comes), and the route removes its part."""
    upd = _update()
    meta = _meta()
    if (upd.job_running() and meta.get("installing")) or _handing_off(upd, meta):
        return False
    if _busy.locked():
        _cancelled.set()
    _clear()
    return True


def _public(meta):
    return {k: meta.get(k) for k in ("id", "version", "notes", "same_version",
                                     "channel", "size")}


def accept(part, config_dir=None, channel=None):
    """Verify the streamed upload and keep it waiting for Install."""
    upd = _update()
    from core.version import VERSION, is_newer               # noqa: PLC0415
    s = upd.settings(config_dir, channel)
    incoming = os.path.join(offline_dir(), "incoming")
    try:
        man = verify_file(part, incoming, s["pubkey"], s["manifest_app"])
        version = str(man.get("version") or "").strip().lstrip("vV")
        if not version:
            raise OfflineError("This update file names no version.")
        same = not is_newer(version, VERSION) and not is_newer(VERSION, version)
        if not same and not is_newer(version, VERSION):
            raise OfflineError(f"This file holds v{version}, older than the "
                               f"v{VERSION} this box runs. To go back, use Roll "
                               "back instead.")
        meta = {"id": secrets.token_hex(8), "version": version,
                "notes": str(man.get("notes_short") or "")[:4000],
                "same_version": same, "channel": s["channel"],
                "size": os.path.getsize(os.path.join(incoming, BUNDLE)),
                "at": time.time(), "boot_id": upd._boot_id()}
        with open(os.path.join(incoming, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(incoming, ready_dir())
    except BaseException:
        _clear()
        raise
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    return _public(meta)


def install(file_id, reinstall=False, config_dir=None, channel=None):
    """Hand the waiting file to the applier as a local-source job."""
    upd = _update()
    from core.version import VERSION, is_newer               # noqa: PLC0415
    meta = _meta()
    if not meta or meta.get("id") != str(file_id or "") or meta.get("installing") \
            or time.time() - float(meta.get("at") or 0) > EXPIRE_S:
        raise OfflineError("That file is no longer waiting to be installed. "
                           "Upload it again.")
    if not upd.ota_ready():
        raise OfflineError("This box is not set up for updates (no release "
                           "layout). Update it from the installer.")
    if upd.job_running():
        raise upd.UpdateBusy("an update is already running")
    version = meta["version"]
    if not is_newer(version, VERSION):
        if is_newer(VERSION, version):
            raise OfflineError(f"v{version} is older than the v{VERSION} this "
                               "box runs. To go back, use Roll back instead.")
        if not reinstall:
            raise OfflineError(f"This box already runs v{version}. Confirm to "
                               "install it again.")
    # The slot is held from the job write to the unit start: a sweep or an
    # upload between "installing" in the meta and the "starting" status
    # would still see the previous job's ended phase and clear ready/.
    if not _busy.acquire(blocking=False):
        raise upd.UpdateBusy("an update file is being uploaded")
    status = {"phase": "starting", "percent": 0, "detail": "installing from a file",
              "version": version, "started": int(time.time()),
              "boot_id": upd._boot_id(), "pid": 0}
    try:
        job = upd._write_job("apply", version, config_dir, meta.get("channel") or channel)
        job.update({"source": "local", "local_dir": ready_dir(), "assets": {},
                    "reinstall": not is_newer(version, VERSION)})
        upd._write_json(upd._p("job.json"), job)
        meta["installing"] = True
        upd._write_json(os.path.join(ready_dir(), "meta.json"), meta)
        upd._stage_applier()
        upd._write_json(upd._p("status.json"), status)
        upd._start_unit()
    except Exception as exc:
        # The unit never took the file (sudo refused, systemd would not start
        # it): left as written, the meta said installing and the status
        # "starting" for EXPIRE_S, so Cancel refused, an upload said "already
        # running" and Install said "upload it again" for an hour (Yaya,
        # 2026-10-05). Take both back, so the file can go or be replaced now.
        meta.pop("installing", None)
        upd._write_json(os.path.join(ready_dir(), "meta.json"), meta)
        upd._write_json(upd._p("status.json"), dict(
            status, phase="failed", detail="the updater did not start", error=str(exc)))
        raise
    finally:
        _busy.release()
    return upd.status()
