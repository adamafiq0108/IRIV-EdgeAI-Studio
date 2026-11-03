# -*- coding: utf-8 -*-
"""
storage_watch.py -- the alarm for an NVMe drive the box expects and does not
have mounted.

Tok Aba 2026-10-05: 12 months of event archives, snapshots and clips is only
for data on the NVMe. When the drive is not mounted the box keeps working on
the built-in storage (the eMMC), keeps that data for 60 days, and SAYS so --
an Inbox notice to the admins and a banner on the dashboard and on Settings,
until the drive is mounted again.

"Expected" is the load-bearing word: a box that never had an NVMe must not
alarm. A drive mount point counts as expected when
  * fstab lists it as the NVMe's mount point (install.sh / setup_nvme.sh
    write that line), or
  * this box has SEEN our data on it -- the store, or a system's data or
    archive dir, under it while it was mounted (remembered in
    storage.local.toml [storage_watch].mounts, on the config dir, which is
    on the eMMC and outlives the drive), or
  * a configured path names it ($CYTRON_STORE, a system's [app].data_dir or
    [storage].archive_dir).

Not mounted is core/config.py mounted(): the mount point's st_dev against its
parent's, two stats. check() runs every minute from the hub's watch thread
(hub_server.py) and is cheap enough to; current() is what the pages read and
re-checks only when the last answer is older than CHECK_EVERY_S.

The guard that nothing is WRITTEN into an unmounted mount point lives with
the code that picks the paths (core/config.py store_dir, the app's
config.data_dir and store.Store); this module only notices and tells.

FORGETTING. The "seen on this drive" memory never expired by itself, so a
box deliberately moved off its NVMe (store back on the eMMC, drive taken
out) alarmed for ever, until someone edited storage.local.toml (Yaya's QA
part 1, bug 8). forget() is the admin's "This box no longer uses the NVMe
drive" on Data Retention: it drops that memory and re-checks. It is an
explicit action, not a timer: a drive that is merely missing for a while is
exactly the case the alarm exists for, and data landing on the eMMC
meanwhile is not "confirmed elsewhere", it is the symptom. What forget()
cannot drop it says: an fstab line or a configured path still naming the
drive keeps it expected, and the answer names which.
"""

import os
import threading
import time

from core import config as config_mod

#: How often the hub's thread looks, and how stale current() may answer.
CHECK_EVERY_S = 60

#: The storage.local.toml table this module owns.
TABLE = "storage_watch"

#: The banner and the Inbox notice, word for word (Tok Aba 2026-10-05).
ALARM_TITLE = "The NVMe drive is not mounted"
ALARM_TEXT = ("The NVMe drive is not mounted. New data is being saved on the "
              "built-in storage and kept for 60 days. Mount the drive again "
              "(or restart the box) to go back to 12 months.")
CLEAR_TITLE = "The NVMe drive is mounted again"
FORGOT_TITLE = "The NVMe drive is no longer expected on this box"
FORGOT_TEXT = ("An admin confirmed on Data Retention that this box no longer "
               "uses the NVMe drive. Event archives, snapshots and clips are "
               "kept on the built-in storage for up to 60 days. The box "
               "expects the drive again the moment data is saved on it.")
CLEAR_TEXT = ("The NVMe drive is mounted again. Restart the box so every "
              "system saves to it again; retention then goes back to the "
              "saved periods (up to 12 months). Anything recorded while the "
              "drive was missing stays on the built-in storage where it was "
              "saved: it is not moved or merged, it is not shown on the "
              "Footage pages, and it is deleted there after 60 days.")

_lock = threading.Lock()
_last = {"at": 0.0, "state": None}


def _norm(path):
    return os.path.normpath(str(path))


def _under(path, point):
    """Whether `path` is `point` or lies below it."""
    p, m = _norm(path), _norm(point)
    return p == m or p.startswith(m.rstrip(os.sep) + os.sep)


def _mount_of(path, points):
    """The nearest of `points` that `path` lies under, mounted or not."""
    best = None
    for point in points:
        if _under(path, point) and (best is None or len(_norm(point)) > len(best)):
            best = _norm(point)
    return best


def _nvme_roots():
    return {_norm(config_mod.NVME_MOUNT),
            _norm(os.environ.get("CYTRON_NVME") or config_mod.NVME_MOUNT)}


def _state(config_dir=None):
    return dict(config_mod.load_local("storage", config_dir).get(TABLE) or {})


def _save_state(state, config_dir=None):
    local = config_mod.load_local("storage", config_dir)
    local[TABLE] = state
    config_mod.save_local("storage", local, config_dir)


def _system_paths(pkg_dirs):
    """(configured, in_use) paths of every system: what its config NAMES
    ([app].data_dir, [storage].archive_dir) and where its data and archive
    dirs resolve to now. Unreadable systems are skipped here: this answers
    "is a drive missing", never "what is this system's cap"."""
    from core.services import detector_control as dc         # noqa: PLC0415
    from core.services import retention                       # noqa: PLC0415
    configured, in_use = [], []
    for pkg in pkg_dirs or []:
        try:
            cfg = dc._merged(pkg)
        except Exception:                                    # noqa: BLE001
            continue
        for value in (((cfg.get("app") or {}).get("data_dir") or ""),
                      ((cfg.get("storage") or {}).get("archive_dir") or "")):
            value = str(value).strip()
            if value and os.path.isabs(value):
                configured.append(value)
        in_use += retention.data_dirs_of_system(pkg) or []
    return configured, in_use


def check(pkg_dirs=None, config_dir=None, notify=None):
    """Look once: is an expected drive not mounted? Raises the alarm (Inbox
    notice to the admins) when it starts and clears it (a second notice)
    when the drive is back; writes storage.local.toml only on a change.

    Returns {"active", "mounts", "title", "text"} -- the banner's input."""
    if pkg_dirs is None:
        from core.services import retention                   # noqa: PLC0415
        pkg_dirs = list(retention._known_pkg_dirs)
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    points = config_mod.guarded_mounts(config_dir)
    nvme = _nvme_roots()
    fstab = {_norm(p) for p in config_mod.fstab_mounts()}
    configured, in_use = _system_paths(pkg_dirs)
    env = os.environ.get("CYTRON_STORE")
    if env:
        configured.append(env)
    try:
        in_use.append(str(config_mod.store_dir()))
    except Exception:                                        # noqa: BLE001
        pass

    with _lock:
        state = _state(config_dir)
        seen = [_norm(m) for m in (state.get("mounts") or [])]
        changed = False
        # Remember every drive our data is on while it IS mounted: that is
        # what makes its absence later an alarm rather than a box without one.
        # An explicit $CYTRON_NVME is looked at here before fstab or this
        # memory names it (guarded_mounts leaves it out until then): this
        # loop is how a drive mounted without an fstab line gets remembered.
        for path in in_use:
            m = _mount_of(path, points + sorted(nvme - set(points)))
            if m and config_mod.mounted(m) and m not in seen:
                seen.append(m)
                changed = True
        expected = set(seen) | (fstab & nvme)
        for path in configured:
            m = _mount_of(path, points)
            if m:
                expected.add(m)
        missing = sorted(m for m in expected if not config_mod.mounted(m))
        was = bool(state.get("alarm"))
        now_on = bool(missing)
        if now_on != was:
            changed = True
            try:
                if now_on:
                    notify("storage", ALARM_TITLE, ALARM_TEXT,
                           config_dir=config_dir, page="settings",
                           tab="retention")
                else:
                    notify("storage", CLEAR_TITLE, CLEAR_TEXT,
                           config_dir=config_dir, page="settings",
                           tab="retention")
            except Exception:                                # noqa: BLE001
                pass            # the banner still says it; the Inbox is extra
        if changed:
            state["mounts"] = seen
            state["alarm"] = now_on
            state["missing"] = missing
            _save_state(state, config_dir)
        result = {"active": now_on, "mounts": missing,
                  "title": ALARM_TITLE if now_on else "",
                  "text": ALARM_TEXT if now_on else ""}
        _last.update(at=time.time(), state=result)
        return result


def forget(config_dir=None, pkg_dirs=None, notify=None):
    """The admin's "This box no longer uses the NVMe drive": drop the
    seen-on-this-drive memory, re-check, and say what still expects it.

    Returns {"forgotten": [mounts dropped], "alarm": check()'s result,
    "still_expected": {mount: why}} -- `why` is "listed in /etc/fstab" or
    "named by a setting ($CYTRON_STORE, a system's data_dir or archive_dir)";
    forgetting cannot undo either, and the page says so instead of a
    banner that comes straight back with no reason given."""
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    with _lock:
        state = _state(config_dir)
        forgotten = [_norm(m) for m in (state.get("mounts") or [])]
        state["mounts"] = []
        _save_state(state, config_dir)
        _last.update(at=0.0, state=None)
    # The re-check posts nothing: "mounted again" would be the wrong words
    # for a drive that is simply not expected any more.
    result = check(pkg_dirs, config_dir, notify=lambda *a, **k: None)
    still = {}
    if result["active"]:
        nvme = _nvme_roots()
        fstab = {_norm(p) for p in config_mod.fstab_mounts()}
        if pkg_dirs is None:
            from core.services import retention               # noqa: PLC0415
            pkg_dirs = list(retention._known_pkg_dirs)
        configured, _in_use = _system_paths(pkg_dirs)
        env = os.environ.get("CYTRON_STORE")
        if env:
            configured.append(env)
        points = config_mod.guarded_mounts(config_dir)
        for m in result["mounts"]:
            if m in fstab and m in nvme:
                still[m] = f"listed in {config_mod.FSTAB}"
            elif any(_mount_of(c, points) == m for c in configured):
                still[m] = ("named by a setting ($CYTRON_STORE, a system's "
                            "data_dir or archive_dir)")
            else:
                still[m] = "expected again: our data is on it now"
    elif forgotten:
        try:
            notify("storage", FORGOT_TITLE, FORGOT_TEXT, config_dir=config_dir,
                   page="settings", tab="retention")
        except Exception:                                    # noqa: BLE001
            pass
    return {"forgotten": forgotten, "alarm": result, "still_expected": still}


def current(config_dir=None, pkg_dirs=None):
    """The alarm as the pages show it: the last check when it is fresh, else
    a new one. Never raises -- a banner that cannot be worked out is no
    banner, not a broken page."""
    if _last["state"] is not None and time.time() - _last["at"] < CHECK_EVERY_S:
        return dict(_last["state"])
    try:
        return check(pkg_dirs, config_dir)
    except Exception:                                        # noqa: BLE001
        return {"active": False, "mounts": [], "title": "", "text": ""}
