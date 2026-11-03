# -*- coding: utf-8 -*-
"""
config.py -- load the system's own config.toml, plus a gitignored local file.

Same layering as the dashboard's config/ (conventions sec 6): the committed
file holds structure and safe defaults, config.local.toml holds secrets and
per-device values, local wins per key, sub-tables merge field-by-field so a
partial override never wipes its siblings.
"""

import os

try:                                   # 3.11+
    import tomllib as _toml

    def _load(path):
        with open(path, "rb") as f:
            return _toml.load(f)
except ImportError:                    # 3.9/3.10
    import toml as _toml               # noqa: F401  (in requirements.txt)

    def _load(path):
        with open(path, "r", encoding="utf-8") as f:
            return _toml.load(f)

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _merge(base, override):
    """Recursive per-key merge; dicts merge, everything else replaces."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(pkg_dir=None):
    """Return the merged config dict. Missing files are not an error -- the
    app must start on a device that has never been configured."""
    pkg_dir = pkg_dir or PKG_DIR
    cfg = {}
    for name in ("config.toml", "config.local.toml"):
        path = os.path.join(pkg_dir, name)
        if os.path.exists(path):
            cfg = _merge(cfg, _load(path))
    return cfg


#: Where an NVMe is expected to be mounted. events.jsonl and the snapshot dir
#: belong here rather than on the eMMC, which wears out under that write traffic
#: (iriv-edge-ai-board). Overridable for a differently-mounted drive.
NVME_ROOT = os.environ.get("CYTRON_NVME", "/mnt/nvme")

#: The one directory under the mount that this software owns. Everything it
#: writes to the drive lives under it -- data, the store, the systems -- and it
#: is what install.sh creates and chowns to the service account. The MOUNT
#: ROOT itself is normally root:root 755, which is correct: a service account
#: has no business writing at the root of a disk.
NVME_DIR = "cytron"


def _nvme_usable():
    """The NVMe root if it is really mounted AND writable, else None. A board
    without the drive (or with it not yet mounted) falls back to the eMMC.

    "Is it a directory and can I write to it" is not enough: /mnt/nvme exists as
    an ordinary empty folder on the root filesystem whenever the drive is absent
    or has not mounted yet, and writing into it succeeds -- straight onto the
    eMMC, which is the wear this whole mechanism exists to avoid. So the default
    path must also be a MOUNT POINT. An explicit CYTRON_NVME is taken at its
    word: pointing it somewhere unmounted is then a deliberate choice.
    """
    root = NVME_ROOT
    if not os.path.isdir(root):
        return None
    if root == "/mnt/nvme" and not os.path.ismount(root):
        return None
    # ...and an explicit one that fstab lists as a drive is held to the same
    # rule: "taken at its word" was never meant to cover a drive that did not
    # mount this boot.
    if _unmounted_mount(root):
        return None
    # Probe WHERE THE WRITES ACTUALLY GO, not the mount root. The root is
    # root:root 755 on a normally installed board -- correct, and nothing here
    # ever writes there: every path this module builds goes under NVME_DIR.
    # Testing the root asked the service account for a permission it is not
    # supposed to have, failed, and reported "no second storage" on a box with
    # a healthy 117 GB drive mounted -- which is what greys out the Snapshots
    # and Clips switches, with a tooltip telling the operator to fit the NVMe
    # they had already fitted.
    #
    # The mount root is still the fallback, for a drive whose directory has
    # not been created yet: then "can I write here" is the real question,
    # because creating it is the next thing that has to happen.
    owned = os.path.join(root, NVME_DIR)
    target = owned if os.path.isdir(owned) else root
    # Unique per probe, same reason as _usable_dir below: this path is
    # shared by EVERY process on the box (three masters answering concurrent
    # polls, plus the apps), and a fixed name let one process delete the
    # probe another had just written -- the loser's remove raised, the NVMe
    # was declared absent, and that request fell back to the empty eMMC dir.
    probe = os.path.join(
        target, f".cytron_write_test.{os.getpid()}.{os.urandom(4).hex()}")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        try:
            os.remove(probe)
        except FileNotFoundError:
            pass
        return root
    except OSError:
        return None


#: The table that says which folders are DRIVES. Module-level so a test can
#: point it at a scratch file.
FSTAB = "/etc/fstab"


def _unmounted_mount(path):
    """The drive mount point `path` lives under while nothing is mounted
    there (the NVMe's, or one fstab lists), else None.

    The same rule as core/config.py unmounted_mount_of, kept here because
    the app imports nothing from core. A folder under an unmounted mount
    point is an ordinary folder on the eMMC: writing there fills the system
    disk, and the drive hides it the moment it mounts again.

    /mnt/nvme is always a drive. An explicit CYTRON_NVME is one only when
    fstab says so: putting it in this list unconditionally judged a root
    pointed at an ordinary folder (a dev box, the smoke tests) "unmounted"
    and _nvme_usable answered None, the opposite of "taken at its word".
    (Core also counts a mount storage_watch remembered data on; the app
    cannot read that memory, which lives on the hub's config dir.)"""
    points = ["/mnt/nvme"]
    try:
        with open(FSTAB, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and not parts[0].startswith("#") \
                        and os.path.isabs(parts[1]) and parts[1] != "/":
                    points.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    p = os.path.normpath(str(path or ""))
    best = ""
    for point in points:
        point = os.path.normpath(point)
        if (p == point or p.startswith(point.rstrip(os.sep) + os.sep)) \
                and len(point) > len(best):
            best = point
    if not best or os.path.ismount(best):
        return None
    return best


#: Storage messages already printed this process. data_dir() is called from
#: the app once, but the MASTER calls it on every status poll (core's
#: detector_control asks the app where data lives) -- an unconditional print
#: there would grow the very log this change is bounding.
_said = set()


def _say_once(message):
    if message not in _said:
        _said.add(message)
        print(message)


def _usable_dir(path):
    """Create `path` if needed and prove a file can be written INSIDE it.

    Returns the OSError on failure, None on success. makedirs alone is not
    enough: with the whole tree already present, makedirs(exist_ok=True)
    succeeds even when the leaf is owned by another user -- and an EXISTING
    root-owned /mnt/nvme/cytron is exactly how a pi-run app ended up silently
    writing 5 GB of events to the SD card while the NVMe sat empty.
    """
    try:
        os.makedirs(path, exist_ok=True)
        # UNIQUE per probe. This used to be one fixed name, and the master
        # answers the snapshots poll and the clips poll CONCURRENTLY: two
        # threads created the same file, one deleted it, the other's delete
        # raised -- and a raised probe declared the NVMe unwritable, so that
        # request fell back to the package's empty data dir and the Footage
        # page read "no images" until the next poll.
        probe = os.path.join(
            path, f".cytron_write_test.{os.getpid()}.{os.urandom(4).hex()}")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        try:
            os.remove(probe)
        except FileNotFoundError:
            pass                        # someone tidied it; the write proved
        return None
    except OSError as exc:
        return exc


def _adopt_legacy_data(chosen, pkg_dir):
    """When the store moves to the NVMe but history exists beside the package,
    carry over the SMALL files the running system needs (labels_*.txt -- the
    detector config points at them by name) and say loudly that the bulk
    (events.jsonl, snapshots, clips) is still at the old path.

    Deliberately NOT an automatic bulk migration: moving gigabytes inside app
    startup on a box whose SD card is nearly full is how a power cut turns a
    full history into half of one. The operator moves the bulk once, with the
    app stopped; until then nothing is deleted and nothing is lost -- the old
    files stay where they are and the master's database already holds the
    ingested history.
    """
    legacy = os.path.join(pkg_dir, "data")
    try:
        if os.path.realpath(legacy) == os.path.realpath(chosen):
            return
        legacy_events = os.path.join(legacy, "events.jsonl")
        if not os.path.exists(legacy_events):
            return
        if os.path.exists(os.path.join(chosen, "events.jsonl")):
            return                       # both exist; the operator already migrated
        import glob as _glob             # noqa: PLC0415
        import shutil as _shutil         # noqa: PLC0415
        for src in _glob.glob(os.path.join(legacy, "labels_*.txt")):
            dst = os.path.join(chosen, os.path.basename(src))
            if not os.path.exists(dst):
                _shutil.copy2(src, dst)
        _say_once(f"[APP] storage: data dir moved to {chosen}; existing "
                  f"history (events.jsonl, snapshots, clips, and any archive "
                  f"beside it) is still at {legacy} and is left untouched - "
                  "stop the app and move it over once to free the old disk")
    except OSError as exc:               # adoption is best-effort, never fatal
        _say_once(f"[APP] storage: legacy data check failed: {exc}")


def data_dir(cfg, pkg_dir=None):
    """Absolute path to this system's data directory (events.jsonl, app.log,
    snapshots, clips), created if absent.

    Storage preference, first that works wins:
      1. an explicit [app].data_dir, IF its target is creatable and writable
      2. the NVMe (/mnt/nvme/cytron/<slug>/data), IF mounted and writable
      3. the package's local ./data on the eMMC -- always available

    So a board WITH an NVMe uses it automatically; one without still runs, with
    the DB's snapshots on the eMMC. No config change needed either way.

    Every candidate is probed by writing INSIDE the actual target directory --
    checking only an ancestor once reported the NVMe usable while a root-owned
    /mnt/nvme/cytron made the real target unwritable. A preferred candidate
    that fails is REPORTED (stdout ends up in app.log / the master's log): a
    wear-protection mechanism that silently stops protecting reads as working
    while it fills the SD card.

    Decided once per process on purpose: the store, the capture writers and
    state.json are all bound to this path at startup, and switching it mid-run
    would split the event log under the ingest cursor. A drive mounted after
    boot is picked up on the next app restart.
    """
    pkg_dir = pkg_dir or PKG_DIR
    app = cfg.get("app") or {}
    slug = app.get("slug") or os.path.basename(pkg_dir)

    candidates = []                      # (label, path)
    rel = app.get("data_dir")
    if rel:
        path = rel if os.path.isabs(rel) else os.path.join(pkg_dir, rel)
        gone = _unmounted_mount(path)
        if gone:
            # Never created in the empty mount point: the local ./data below
            # is the defined place on the built-in storage, and the next app
            # start after the drive mounts goes back to the configured dir.
            _say_once(f"[APP] storage: [app].data_dir {path} is on {gone}, "
                      "which is NOT mounted; saving to the built-in storage "
                      f"({os.path.join(pkg_dir, 'data')}) until the drive is "
                      "mounted and the app restarts")
        else:
            candidates.append(("[app].data_dir", path))
    nvme = _nvme_usable()
    if nvme:
        candidates.append(("NVMe", os.path.join(nvme, "cytron", slug, "data")))
    candidates.append(("local", os.path.join(pkg_dir, "data")))   # eMMC, last resort

    for i, (label, path) in enumerate(candidates):
        err = _usable_dir(path)
        if err is None:
            if label == "NVMe":
                _adopt_legacy_data(path, pkg_dir)
            return path
        if i + 1 < len(candidates):
            _say_once(f"[APP] storage: {label} data dir {path} is not writable "
                      f"({err}); falling back to {candidates[i + 1][1]}"
                      + (" - fix the ownership (e.g. sudo chown of the parent) "
                         "to get event/log writes off the eMMC"
                         if label == "NVMe" else ""))
    # Every candidate failed (should not happen); make the local one and use it.
    fallback = os.path.join(pkg_dir, "data")
    os.makedirs(fallback, exist_ok=True)
    return fallback


#: Where AI models belong. The operator's ask is one folder on the NVMe --
#: /mnt/nvme/model -- for built-in and hand-copied models alike, so model
#: reads (and downloads) stay off the eMMC and live in one place. The legacy
#: drop folder keeps working: nothing is moved, and resolution falls back
#: through every known root (see model_roots()).
NVME_MODEL_DIR = os.path.join(NVME_ROOT, "model")
LEGACY_MODEL_DIR = "/home/pi/model"
#: Where the PRODUCT keeps the models it downloads, kept apart from the
#: operator's drop folder above (core/config.py BUILTIN_MODEL_DIR says why).
#: This app imports nothing from core/, so the path is repeated rather than
#: shared -- the two must be changed together.
BUILTIN_MODEL_DIR = os.environ.get("CYTRON_BUILTIN_MODEL_DIR",
                                   "/opt/irivai-models")


def model_roots():
    """Every place a model may live, preferred first: an explicit
    CYTRON_MODEL_DIR, the product's own model folder, the NVMe's model folder,
    the legacy /home/pi/model. Only roots that exist are returned; order is
    preference.

    The built-in folder is listed AND the NVMe one is kept: a box that fetched
    models before the built-in/custom split has them on the NVMe, and dropping
    that root would strand a detector on a path the dashboard itself wrote.
    """
    roots = []
    explicit = os.environ.get("CYTRON_MODEL_DIR")
    if explicit:
        roots.append(explicit)
    if os.path.isdir(BUILTIN_MODEL_DIR):
        roots.append(BUILTIN_MODEL_DIR)
    if os.path.isdir(NVME_MODEL_DIR) and (
            NVME_ROOT != "/mnt/nvme" or os.path.ismount(NVME_ROOT)):
        roots.append(NVME_MODEL_DIR)
    if os.path.isdir(LEGACY_MODEL_DIR):
        roots.append(LEGACY_MODEL_DIR)
    return roots
