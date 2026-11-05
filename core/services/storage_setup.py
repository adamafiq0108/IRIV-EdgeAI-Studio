# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
storage_setup.py -- "New storage detected": noticing a drive fitted after the
box was installed, asking the admin once, and setting it up through the root
helper.

Tok Aba 2026-10-07: a box with no NVMe (or extra disk) that later gets one
should say so in a popup -- use it for storing files? format it? -- instead
of leaving it unused until somebody runs scripts/setup_nvme.sh over SSH.

DETECTION. The hub's thread (hub_server.py) calls poll() every POLL_S
seconds: one unprivileged `lsblk -J`, filtered by the SAME eligible() the
root helper gates on (scripts/storage_setup.py, imported from the release
tree -- one rule, tested once). A drive is offered only when /mnt/nvme has
nothing mounted on it: a box that already has its storage is not asked
again. Each drive the box has not seen before gets one Inbox notice to the
admins; the popup itself is drawn by the dashboard from prompt().

"NO" IS REMEMBERED per drive identity (WWN, serial, filesystem UUID -- not
sda, which changes with the slot), in storage.local.toml [storage_setup],
so the popup does not nag on every page; a different drive asks again. The
"Set up storage" button on Data Retention lists every offerable drive,
declined or not.

SETTING UP is core/services/storage_job.py: the root helper in a
background thread, its STEP lines the popup's progress.

Nothing here decides where data goes: the helper mounts the drive at
/mnt/nvme, which is where the store, the apps and storage_watch already
look (core/config.py NVME_MOUNT). Systems move their data there at their
next start -- hence the reboot the popup asks for.
"""

import importlib.util
import json
import shutil
import subprocess
import threading
import time

from core import config as config_mod

#: The shared rules: the release tree's copy of the helper. Advice here, the
#: gate in the root-owned copy.
SHARED = config_mod._REPO_ROOT / "scripts" / "storage_setup.py"
#: How often the hub's thread looks at lsblk.
POLL_S = 5
#: The storage.local.toml table this module owns.
TABLE = "storage_setup"
#: Bounded memory: a box that has seen fifty sticks forgets the oldest.
KEEP = 50

_lock = threading.Lock()
_cache = {"at": 0.0, "devices": []}
_shared_mod = None


def shared():
    """scripts/storage_setup.py as a module (its pure half)."""
    global _shared_mod
    if _shared_mod is None:
        spec = importlib.util.spec_from_file_location("irivai_storage_setup", str(SHARED))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _shared_mod = mod
    return _shared_mod


def _run(argv, input=None, timeout=20):
    return subprocess.run(argv, input=input, capture_output=True, text=True,
                          timeout=timeout)


def scan(run=None, root_names=None):
    """Every drive this box could set up, freshly asked of lsblk. [] where
    there is no lsblk (a Windows dev box) -- never raises."""
    mod = shared()
    run = run or _run
    if run is _run and not shutil.which("lsblk"):
        return []
    try:
        tree = mod.lsblk(run)
    except Exception:                                        # noqa: BLE001
        return []
    names = mod.root_names() if root_names is None else root_names
    return mod.mark_expected(mod.eligible(tree, names, config_mod.NVME_MOUNT)[0],
                             _fstab())


def _fstab():
    try:
        with open(config_mod.FSTAB, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _state(config_dir=None):
    return dict(config_mod.load_local("storage", config_dir).get(TABLE) or {})


def _save_state(state, config_dir=None):
    local = config_mod.load_local("storage", config_dir)
    local[TABLE] = state
    config_mod.save_local("storage", local, config_dir)


def _store_has_drive():
    return config_mod.mounted(config_mod.NVME_MOUNT)


def poll(config_dir=None, run=None, notify=None, root_names=None):
    """One look (the hub's thread): refresh the cached list and post one
    Inbox notice for each offerable drive never announced before. Returns
    the list."""
    devices = scan(run, root_names)
    with _lock:
        _cache.update(at=time.time(), devices=devices)
    if not devices or _store_has_drive():
        return devices
    if notify is None:
        from core.services import inbox                       # noqa: PLC0415
        notify = inbox.notify_admins
    with _lock:
        state = _state(config_dir)
        told = list(state.get("announced") or [])
        new = [d for d in devices if d["id"] not in told]
        if not new:
            return devices
        for d in new:
            try:
                notify("storage", "New storage detected",
                       "%s, %s (%s) was plugged in. A master account can set it "
                       "up for storing files from the popup, or later from "
                       "Settings > Data Retention > Set up storage."
                       % (d["model"], d["size_text"], d["kind"]),
                       config_dir=config_dir, page="settings", tab="retention")
            except Exception:                                # noqa: BLE001
                pass
            told.append(d["id"])
        state["announced"] = told[-KEEP:]
        _save_state(state, config_dir)
    return devices


def devices(max_age=POLL_S * 2):
    """The cached list, refreshed when stale (a hub whose thread is not
    running: tests, a dev box)."""
    if time.time() - _cache["at"] > max_age:
        found = scan()
        with _lock:
            _cache.update(at=time.time(), devices=found)
    return list(_cache["devices"])


def declined(config_dir=None):
    return list(_state(config_dir).get("declined") or [])


def prompt(config_dir=None, found=None):
    """The drive the popup should ask about now, or None: the first one
    offerable, not declined, while /mnt/nvme has no drive. A drive an active
    /etc/fstab line names is the box's own storage drive that did not
    mount: still asked about, labelled so (`expected`), and offered Mount
    only -- the helper refuses to format it (Pipi, 2026-10-07)."""
    if _store_has_drive():
        return None
    found = devices() if found is None else found
    no = set(declined(config_dir))
    for d in found:
        if d["id"] in no:
            continue
        return d
    return None


_inspected = {}                     # drive id -> the helper's answer


def inspect(dev_id, run=None, root_names=None):
    """{"foreign", "message"} for the popup, before "use it as it is" is
    offered: does the drive already hold another box's IRIV store? Asked of
    the root helper (a read-only look); a drive that is not usable as it
    is, or is the box's own (expected), needs no look. Remembered per drive
    id: an unmounted drive's content does not change under us."""
    from core.services import storage_job                     # noqa: PLC0415
    dev = next((d for d in scan(run, root_names) if d["id"] == dev_id), None)
    if dev is None:
        raise ValueError("that drive is not here any more, or may not be set up")
    if not dev["usable"] or dev.get("expected"):
        return {"foreign": False, "message": ""}
    if dev_id in _inspected:
        return dict(_inspected[dev_id])
    if run is None and not storage_job.can_setup():
        return {"foreign": False, "message": ""}            # the helper refuses later
    req = {"action": "inspect", "disk": dev["name"], "id": dev["id"], "size": dev["size"]}
    r = (run or storage_job._sudo)(list(storage_job.HELPER_ARGV), input=json.dumps(req))
    done = [ln[5:] for ln in (r.stdout or "").splitlines() if ln.startswith("DONE ")]
    if r.returncode != 0 or not done:
        tail = (r.stderr or "").strip().splitlines()
        raise ValueError((tail[-1] if tail else "the drive could not be looked at")
                         .replace("storage-setup: ", "", 1))
    out = json.loads(done[-1])
    _inspected[dev_id] = {"foreign": bool(out.get("foreign")),
                          "message": str(out.get("message") or "")}
    return dict(_inspected[dev_id])


def decline(dev_id, config_dir=None):
    """The admin's "No": remembered for this drive only."""
    dev_id = str(dev_id or "").strip()[:200]
    if not dev_id:
        raise ValueError("which drive?")
    with _lock:
        state = _state(config_dir)
        no = [d for d in (state.get("declined") or []) if d != dev_id]
        no.append(dev_id)
        state["declined"] = no[-KEEP:]
        _save_state(state, config_dir)
    return declined(config_dir)
