# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
config.py -- Deployment config loading.

Five surfaces in config/: brand, storage, auth, systems, slaves. Anything a
second customer would need different lives here, never in code
(docs/conventions.md sec 6).

Layering: a committed <name>.toml holds structure and safe defaults, a
gitignored <name>.local.toml holds secrets and per-device values, and local
wins per key. Sub-tables merge field-by-field, so overriding one password does
not wipe its siblings.
"""

import functools
import os
import pathlib
import re
import stat
import sys
import threading

from core import tomlcache

try:
    import tomllib                     # Python 3.11+
except ModuleNotFoundError:            # pragma: no cover
    import tomli as tomllib

_CONFIG_DIR = pathlib.Path(
    os.environ.get("CYTRON_CONFIG_DIR",
                   pathlib.Path(__file__).resolve().parent.parent / "config")
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


#: Default store roots, PREFERENCE order (the NVMe first). Module-level so
#: tests can point them at scratch directories. $CYTRON_STORE outranks both --
#: an explicit choice is taken at its word.
STORE_CANDIDATES = [
    "/mnt/nvme/cytron/store",
    str(pathlib.Path.home() / ".local" / "share" / "irivai-systor"),
]

#: Dropped into a chosen store root so it keeps counting as a store even when
#: everything in it is deleted -- a root that WAS the store must never lose an
#: existence contest to a preferred empty one just because it is empty today.
STORE_MARKER = ".cytron_store"

#: Content that makes a directory a store. The marker plus each content dir
#: with anything in it.
_STORE_SUBDIRS = ("systems", "layouts", "workflows", "templates",
                  "history", "images")

_store_cache = {}                     # env value -> resolved Path
_store_said = set()                   # once-per-process notices

#: The NVMe's mount point and the table that promises it. A candidate under
#: the mount point while nothing is mounted there is an ordinary folder on
#: the eMMC wearing a disguise (the models dir rule, _nvme_model_dir), and
#: when fstab lists the mount the honest word for that state is "not
#: mounted" -- on one board a UUID mismatch left the drive unmounted and the
#: boot line said the preferred store was "empty", which sent the operator
#: looking for lost content instead of at the mount.
NVME_MOUNT = "/mnt/nvme"
FSTAB = "/etc/fstab"


def _nvme_in_fstab():
    try:
        with open(FSTAB, encoding="utf-8") as f:
            return any(line.split()[1:2] == [NVME_MOUNT] for line in f
                       if line.strip() and not line.lstrip().startswith("#"))
    except (OSError, IndexError):
        return False


def fstab_mounts():
    """The mount points /etc/fstab promises, the root filesystem and swap
    left out. Read on every call: it is one small file, and a line added
    while the hub runs must count from the next check, not the next boot."""
    out = []
    try:
        with open(FSTAB, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2 or parts[0].startswith("#"):
                    continue
                point = parts[1].replace("\\040", " ")
                if os.path.isabs(point) and point != "/":
                    out.append(point)
    except OSError:
        pass
    return out


def mounted(path):
    """Whether a drive is mounted AT `path`: its st_dev differs from its
    parent's (os.path.ismount, the same test findmnt makes). Two stats,
    cheap enough for a check every minute. Looked up at call time so tests
    can stand in a fake."""
    return os.path.ismount(str(path))


def guarded_mounts(config_dir=None):
    """Every mount point a path of ours may live under that is a DRIVE, not
    a folder: the NVMe's, whatever fstab lists, and every mount storage_watch
    has seen our data on while it was mounted (its "expected" rule, read
    from storage.local.toml under `config_dir`, resolved as load_local does).
    While one of these is not mounted, the directory there is an ordinary
    folder on the eMMC, and nothing may be written under it.

    An explicit $CYTRON_NVME is NOT a drive by itself: it is taken at its
    word (the app's _nvme_usable, _nvme_model_dir), and listing it here
    unconditionally held back a store under a root that names an ordinary
    folder, on a box that never had the drive. It counts the moment fstab
    lists it or data was seen on it mounted."""
    points = [NVME_MOUNT]
    points += fstab_mounts()
    try:
        points += [str(m) for m in (
            load_local("storage", config_dir).get("storage_watch") or {}
        ).get("mounts") or ()]
    except (OSError, ValueError):
        pass
    seen, out = set(), []
    for p in points:
        p = os.path.normpath(p)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def unmounted_mount_of(path, points=None, config_dir=None):
    """The guarded mount point `path` lives under while nothing is mounted
    there, else None. The NEAREST guarded point decides: a path under a
    mounted /mnt/nvme/ssd2 is on that drive whatever /mnt/nvme is doing.
    `config_dir` reaches guarded_mounts when `points` is not given."""
    if not path:
        return None
    p = pathlib.PurePath(os.path.normpath(str(path)))
    best = None
    for point in (guarded_mounts(config_dir) if points is None else points):
        root = pathlib.PurePath(os.path.normpath(str(point)))
        if (p == root or root in p.parents) and (
                best is None or len(root.parts) > len(best.parts)):
            best = root
    if best is None or mounted(str(best)):
        return None
    return str(best)


def _store_unmounted(path):
    """True when `path` lives under a drive's mount point and nothing is
    mounted there: never a store to adopt, and worth a line saying why."""
    return unmounted_mount_of(path) is not None


def _store_mount_note():
    """One boot-time line about an NVMe that fstab promises and the kernel
    did not deliver; nothing when the drive is mounted or never listed."""
    if _nvme_in_fstab() and not os.path.ismount(NVME_MOUNT):
        _store_say(f"[STORE] {NVME_MOUNT} is listed in {FSTAB} but NOT "
                   "mounted - check the UUID (blkid) against the fstab line "
                   "and run `sudo mount -a`; until then the store stays on "
                   "the system disk")


def _store_has_content(path):
    """Whether this root already IS a store: the marker, or any user content.
    EXISTENCE is what this measures -- never writability, never preference."""
    p = pathlib.Path(path)
    try:
        if (p / STORE_MARKER).exists():
            return True
        for sub in _STORE_SUBDIRS:
            d = p / sub
            if d.is_dir() and any(d.iterdir()):
                return True
    except OSError:
        pass
    return False


def _store_writable(path):
    p = pathlib.Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".cytron_write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _store_say(message):
    """Say it once, and say it on STDERR.

    Stderr because this is a diagnostic about a path, and the callers that
    matter are asking a QUESTION on stdout: `python -c 'print(store_dir())'`
    is how scripts/install.sh learns where to put the update workspace. On
    stdout the banner became part of the answer, and a real appliance ended up
    with a systemd unit whose ExecStart, StandardOutput and Environment all
    began "[STORE] WARNING:" -- an updater that could never have run, written
    by an installer that reported success.
    """
    if message not in _store_said:
        _store_said.add(message)
        print(message, file=sys.stderr)


def _store_adopt(p):
    """Stamp the chosen root (marker + location pointer) and report a move.

    The pointer lives in the HOME candidate -- the one path that exists on
    every box -- and records where the store resolved last time, so the store
    RELOCATING between runs is a detected, printed event rather than something
    the operator discovers as empty screens. Best-effort: bookkeeping must
    never take the store down."""
    try:
        (p / STORE_MARKER).touch(exist_ok=True)
    except OSError:
        pass
    try:
        home = pathlib.Path(STORE_CANDIDATES[-1])
        pointer = home / ".store_location"
        previous = ""
        try:
            previous = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if previous and pathlib.Path(previous) != p:
            _store_say(f"[STORE] the store root moved: {previous} -> {p}. "
                       "If this was not deliberate, set CYTRON_STORE or put "
                       "the content back.")
        home.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(p), encoding="utf-8")
    except OSError:
        pass


def store_dir():
    """Persistent root for USER-GENERATED content -- systems the operator
    creates, their dashboard layouts, workflows, templates, history, images.
    Deliberately OUTSIDE the repo checkout so a `git pull` or a redeploy never
    overwrites or loses it.

    Resolution -- EXISTENCE BEATS PREFERENCE:
      1. $CYTRON_STORE: the operator said so; taken at its word.
      2. A candidate that already CONTAINS a store wins over an empty one,
         whatever the preference order says. This is the load-bearing rule:
         "first writable wins" once re-homed a box's entire store to a
         freshly-writable empty /mnt/nvme/cytron/store (a chown made it
         writable mid-life) and every system, layout and workflow on the box
         silently vanished from the dashboard -- same defect as the models
         dir, bigger blast radius. A store never relocates as a side effect
         of a directory becoming writable.
      3. Both candidates populated: NOT silently merged or picked -- the
         preferred one is used so the box still boots, and a loud, repeated-
         once-per-process report names both roots and tells the operator to
         consolidate deliberately (or pin CYTRON_STORE). No automatic
         migration: merging two populated stores by guesswork is how content
         gets overwritten.
      4. Nothing anywhere (a fresh box): first WRITABLE candidate in
         preference order -- the NVMe when it is usable, exactly as before.

    The chosen root is stamped (.cytron_store) so it keeps winning even after
    its content is legitimately emptied, and recorded (.store_location in the
    home candidate) so a move between runs is detected and printed."""
    env = os.environ.get("CYTRON_STORE")
    key = (env or "") + "|" + "|".join(STORE_CANDIDATES)
    cached = _store_cache.get(key)
    if cached is not None:
        return cached

    chosen = None
    if env and _store_unmounted(env):
        # Taken at its word EXCEPT here. A CYTRON_STORE on a drive that did
        # not mount is the empty mount point on the eMMC, and the writability
        # probe would happily create the store there: new content fills the
        # system disk and is hidden under the drive the moment it mounts. The
        # store falls back to the built-in one below (the home candidate) and
        # comes back to the drive at the first start after it is mounted.
        _store_say(f"[STORE] WARNING: CYTRON_STORE={env} is on "
                   f"{unmounted_mount_of(env)}, which is NOT mounted - "
                   "nothing is written into the empty mount point; using the "
                   "store on the built-in storage until the drive is mounted "
                   "and the hub restarts")
        env = None
    if env:
        if _store_writable(env):
            chosen = pathlib.Path(env)
        else:
            _store_say(f"[STORE] WARNING: CYTRON_STORE={env} is not usable; "
                       "falling back to the default resolution")
    if chosen is None:
        _store_mount_note()
        # An unmounted NVMe candidate is skipped outright: whatever is under
        # the mount point is on the eMMC, and adopting it would plant the
        # store on the disk the drive was fitted to spare.
        candidates = [c for c in STORE_CANDIDATES if not _store_unmounted(c)]
        populated = [c for c in candidates if _store_has_content(c)]
        if len(populated) == 1:
            chosen = pathlib.Path(populated[0])
            if populated[0] != STORE_CANDIDATES[0]:
                why = ("is NOT mounted" if _store_unmounted(STORE_CANDIDATES[0])
                       else "is empty")
                _store_say(f"[STORE] using the existing store at {chosen} "
                           f"(the preferred {STORE_CANDIDATES[0]} {why}; "
                           "move the content there deliberately and restart "
                           "if you want it on that drive)")
            if not _store_writable(chosen):
                _store_say(f"[STORE] WARNING: the store at {chosen} is not "
                           "writable; fix its ownership - content will NOT "
                           "be moved automatically")
        elif len(populated) > 1:
            chosen = pathlib.Path(populated[0])
            _store_say("[STORE] WARNING: more than one store root has "
                       f"content: {', '.join(populated)}. Using {chosen}; "
                       "consolidate the others into it deliberately (or set "
                       "CYTRON_STORE) - they are NOT merged automatically")
        else:
            for cand in candidates:
                if _store_writable(cand):
                    chosen = pathlib.Path(cand)
                    break
        if chosen is None:
            chosen = _REPO_ROOT / "var"    # last resort (gitignored)
            chosen.mkdir(parents=True, exist_ok=True)

    _store_adopt(chosen)
    _store_cache[key] = chosen
    return chosen


def generated_systems_dir():
    """Where hub-created system packages live -- persistent, outside the repo."""
    d = store_dir() / "systems"
    d.mkdir(parents=True, exist_ok=True)
    return d


def layouts_dir():
    """Where Builder dashboard layouts live -- persistent, outside the repo."""
    d = store_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workflows_dir():
    """Where workflow graphs (the visual pipeline editor) live -- persistent,
    outside the repo, same doctrine as layouts: user-generated JSON a git pull
    must never lose."""
    d = store_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def system_images_dir():
    """Where operator-uploaded system cover images live -- persistent, outside
    the repo, so every browser (and every future deploy) sees the same image."""
    d = store_dir() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nvme_model_dir():
    """/mnt/nvme/model when the NVMe is REALLY mounted and writable, else None.

    Mirrors the app's storage rule (systems/*/app/config.py): the default
    mount point must be a mount point -- an unmounted /mnt/nvme is an ordinary
    folder on the eMMC wearing a disguise -- and writability is proven in the
    TARGET directory, because an ancestor being writable says nothing about a
    root-owned leaf. An explicit CYTRON_NVME is taken at its word.
    """
    root = os.environ.get("CYTRON_NVME", "/mnt/nvme")
    if not os.path.isdir(root):
        return None
    if root == "/mnt/nvme" and not os.path.ismount(root):
        return None
    target = pathlib.Path(root) / "model"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".cytron_write_test"
        probe.write_text("")
        probe.unlink()
        return target
    except OSError:
        return None


#: Where the product's OWN models live -- the ones it downloads from the
#: catalogue. On the eMMC, beside the releases rather than inside one: an OTA
#: update replaces the release directory and prunes old ones, so a model kept
#: in there would be re-downloaded after every update.
#:
#: Deliberately NOT /mnt/nvme/model. That folder is the OPERATOR's -- anything
#: in it is listed as a custom model (services/local_models.py) -- so a
#: catalogue model fetched there showed up twice: once in Default Model where
#: it belongs, and again under Custom Models as an unlabelled drop-in with no
#: manifest. Two categories, two directories.
#:
#: The eMMC is the right home for these despite the usual "keep writes off the
#: boot media" rule: that rule is about the event database and the snapshot
#: stream, which write continuously. A HEF is written once and then only read.
BUILTIN_MODEL_DIR = "/opt/irivai-models"


def builtin_models_dir():
    """The catalogue's own model directory, or None where it is not usable.

    Not created speculatively on a developer box: the appliance installer makes
    it (owned by the service account), and a checkout running from a home
    directory has no business writing into /opt.
    """
    explicit = os.environ.get("CYTRON_BUILTIN_MODEL_DIR")
    target = pathlib.Path(explicit or BUILTIN_MODEL_DIR)
    try:
        if not explicit and not target.is_dir():
            return None                  # installer-made; do not conjure it
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".cytron_write_test"
        probe.write_text("")
        probe.unlink()
        return target
    except OSError:
        return None


def shared_models_dir():
    """ONE models directory every system shares, so a HEF (yolov8n.hef, …) is
    fetched once and reused, not re-downloaded per system. Prefers the
    product's own model folder on the eMMC (/opt/irivai-models, kept clear of
    the operator's NVMe drop folder); then the NVMe's model folder for a box
    installed before that split, so nothing already fetched moves; then an
    existing systems/demo/models (the usual first fetch target on boards
    without the drive); otherwise a persistent dir in the store. New systems
    point [detector.hailo].models_dir here. Models already sitting in an older
    location are not moved -- stored configs keep their absolute paths, and
    the app's resolver falls back through every root."""
    builtin = builtin_models_dir()
    if builtin is not None:
        return builtin
    nvme = _nvme_model_dir()
    if nvme is not None:
        return nvme
    demo_models = _REPO_ROOT / "systems" / "demo" / "models"
    try:
        if demo_models.is_dir() and any(demo_models.iterdir()):
            return demo_models
    except OSError:
        pass
    d = store_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_search_roots():
    """Every directory a model may legitimately sit in, preferred first.

    THE WRITE TARGET IS NOT THE SEARCH PATH. shared_models_dir() answers
    "where should a new download go" -- one directory, the NVMe when present.
    This answers "where might a model already BE", and it must include every
    root any release has ever written or read: preferring the NVMe made an
    upgraded box with an EMPTY /mnt/nvme/model report every already-fetched
    model as "not downloaded", because installed() derived its roots from the
    single write target. Existence on disk, not preference, decides whether a
    model is installed. Roots are returned whether or not they currently
    exist -- callers os.path.exists() the actual file.
    """
    roots = []

    def _add(path):
        path = str(path or "")
        if path and path not in roots:
            roots.append(path)

    try:
        _add(shared_models_dir())              # today's write target
    except OSError:
        pass
    # Named whether or not it is today's target: a box that fetched models
    # BEFORE the built-in/custom split has them on the NVMe, and one installed
    # after has them here. Both must keep resolving, or an update would report
    # every already-fetched model as "not downloaded".
    _add(os.environ.get("CYTRON_BUILTIN_MODEL_DIR") or BUILTIN_MODEL_DIR)
    nvme_root = os.environ.get("CYTRON_NVME", "/mnt/nvme")
    _add(os.path.join(nvme_root, "model"))     # the NVMe home, even if not chosen
    _add(_REPO_ROOT / "systems" / "demo" / "models")   # the historical fetch target
    try:
        _add(store_dir() / "models")           # the pre-NVMe shared fallback
    except OSError:
        pass
    _add(os.environ.get("CYTRON_MODEL_DIR"))   # explicit drop-folder override
    _add("/home/pi/model")                     # the legacy hand-copied folder
    return roots


def _merge(base, override):
    """Deep-merge override onto base. Dict values merge; anything else replaces."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        # Never fall back to defaults on a malformed file: a typo would boot a
        # deployment with the wrong brand or the wrong database, silently.
        raise ValueError(f"{path} is not valid TOML: {e}") from e


def root(config_dir=None):
    """The directory config is read from: the explicit one, or the default."""
    return pathlib.Path(config_dir) if config_dir else _CONFIG_DIR


#: (config dir, name) -> ((stamp of <name>.toml, stamp of <name>.local.toml),
#: merged document). See load().
_load_cache = {}
_load_lock = threading.Lock()


def load(name, config_dir=None):
    """Load config/<name>.toml layered with config/<name>.local.toml.

    Cached until either file changes on disk (core/tomlcache.py stamps: mtime
    and size). It was an lru_cache with no check at all, so a MASTER kept the
    systems / network / storage config it read at start for as long as it
    ran, while the hub rewrote those files underneath it (perf item L3 found
    it). The same shared, read-only document is returned to every caller,
    exactly as the lru_cache did. load.cache_clear() still drops it all, and
    save_local() calls it for this process's own writes.
    """
    base = root(config_dir)
    p_base, p_local = base / f"{name}.toml", base / f"{name}.local.toml"
    key = (str(base), name)
    stamps = (tomlcache.stamp(p_base), tomlcache.stamp(p_local))
    with _load_lock:
        hit = _load_cache.get(key)
    if hit is not None and hit[0] == stamps:
        return hit[1]
    data = _merge(_read(p_base), _read(p_local))
    with _load_lock:
        _load_cache[key] = (stamps, data)
    return data


def _load_cache_clear():
    with _load_lock:
        _load_cache.clear()


load.cache_clear = _load_cache_clear


def load_local(name, config_dir=None):
    """Load only config/<name>.local.toml -- the machine-writable half.

    A caller adding one key must start here rather than from load(), because
    save_local() rewrites the file wholesale: handing it a merged dict would
    copy the committed file's structure and defaults into the local override,
    freezing today's defaults for a deployment that should follow them.

    Uncached on purpose. save_local() clears load()'s cache; a second cache is
    a second thing to forget to invalidate.
    """
    return _read(root(config_dir) / f"{name}.local.toml")


def brand(config_dir=None):
    return load("brand", config_dir)


def storage(config_dir=None):
    return load("storage", config_dir)


def auth(config_dir=None):
    return load("auth", config_dir)


def systems(config_dir=None):
    return load("systems", config_dir)


def slaves(config_dir=None):
    return load("slaves", config_dir)


def monitoring(config_dir=None):
    return load("monitoring", config_dir)


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # A dict inside an inline array (a zone's actions list) must render as
        # an inline table -- str()-ing it would write a Python repr that never
        # reads back.
        return _toml_inline(value)
    if isinstance(value, (list, tuple)):
        # [systems].enabled is a list; str()-ing it would write a quoted Python
        # repr that reads back as one string and iterates as characters.
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    # A TOML basic string cannot hold a literal newline, tab or control char, so
    # a multi-line value (the email body) must escape them or the file we write
    # is one we cannot read back. Backslash first, or it doubles the escapes the
    # later replacements add.
    escaped = (str(value)
               .replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("\n", "\\n")
               .replace("\r", "\\r")
               .replace("\t", "\\t"))
    # Every OTHER control character (U+0000-U+001F, U+007F) is refused by a
    # TOML reader when it appears raw, so one stray byte in a label -- pasted
    # from a document, or planted in a published template file -- would make
    # the whole file unreadable, and a file this writer cannot read back is a
    # file no page can repair. Escaped as \uXXXX, which reads back as the same
    # character.
    escaped = _TOML_CONTROL_RE.sub(lambda m: "\\u%04X" % ord(m.group()), escaped)
    return f'"{escaped}"'


#: Control characters a TOML basic string may not hold raw, minus the three
#: (\n \r \t) _toml_value already spells as their short escapes.
_TOML_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _toml_key(key):
    return key if key.replace("_", "").replace("-", "").isalnum() else _toml_value(key)


def _is_table_array(value):
    """A TOML array-of-tables: a non-empty list whose every item is a dict, e.g.
    [[zones]]. A list of scalars (or of lists, like zone points) is NOT one -- it
    is an ordinary inline array and _toml_value handles it."""
    return (isinstance(value, (list, tuple)) and len(value) > 0
            and all(isinstance(item, dict) for item in value))


def _toml_dump(data, prefix=""):
    """Serialise nested dicts of scalars to TOML. tomllib is read-only, and the
    local files this writes hold flat sections of scalars, sub-tables and
    arrays-of-tables (zones) -- a dependency for that would be heavier than these
    lines."""
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    table_arrays = {k: v for k, v in data.items() if _is_table_array(v)}
    scalars = {k: v for k, v in data.items()
               if not isinstance(v, dict) and not _is_table_array(v)}

    out = []
    if scalars or (prefix and not tables and not table_arrays):
        if prefix:
            out.append(f"[{prefix}]")
        out.extend(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in scalars.items())
    for key, value in tables.items():
        name = f"{prefix}.{_toml_key(key)}" if prefix else _toml_key(key)
        section = _toml_dump(value, prefix=name)
        if section:
            out.append("")
            out.append(section)
    for key, items in table_arrays.items():
        name = f"{prefix}.{_toml_key(key)}" if prefix else _toml_key(key)
        for item in items:
            out.append("")
            out.append(f"[[{name}]]")
            # Scalars first; nested dicts (a function's params/trigger) become
            # INLINE tables -- recursing with _toml_dump here would emit them
            # as detached top-level [sections] that read back onto the wrong
            # table entirely.
            for k, v in item.items():
                if isinstance(v, dict):
                    out.append(f"{_toml_key(k)} = {_toml_inline(v)}")
                else:
                    out.append(f"{_toml_key(k)} = {_toml_value(v)}")
    return "\n".join(out).strip()


def _toml_inline(d):
    """A dict as a TOML inline table: { a = 1, b = "x" }. Used for sub-tables
    of an array-of-tables item, where a bracketed section cannot express the
    nesting on a rewrite."""
    parts = []
    for k, v in d.items():
        rendered = _toml_inline(v) if isinstance(v, dict) else _toml_value(v)
        parts.append(f"{_toml_key(k)} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def toml_text(data):
    """Serialise a nested dict of scalars/tables to TOML text. Public so code
    that writes a local file OUTSIDE config/ (a system package's own
    config.local.toml) can use the same writer save_local() does, rather than
    add a toml-writer dependency for a handful of scalar lines."""
    return _toml_dump(data) + "\n"


def save_local(name, data, config_dir=None):
    """Rewrite config/<name>.local.toml from `data` and drop the load cache.

    Only the gitignored local file is ever written -- the committed file is
    structure and defaults, edited by humans. Comments in the local file do not
    survive a rewrite, which is why machine-managed entries belong here and
    nowhere else.
    """
    base = root(config_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.local.toml"
    # Written to a sibling temp file and swapped in atomically: a crash or a
    # concurrent reader mid-write must never observe a truncated config --
    # these files hold accounts and tokens, and a torn users file is a box
    # nobody can log in to.
    # Tokens live in these files, so the temp file is CREATED 0600 (O_EXCL,
    # never an existing file or a planted link): writing it and then
    # chmod'ing it left it at the umask's 0644 for the length of the write, for good if the
    # write raised (Pipi S2-I2). Its name is unique to the process and call,
    # so a crashed write's leftover never blocks the next one.
    text = _toml_dump(data) + "\n"
    tmp = base / f"{name}.local.toml.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    load.cache_clear()


def tighten_secrets(config_dir=None, systems_dirs=()):
    """Make every secret-holding local file owner-only (0600), at start-up.

    New writes are created 0600 (save_local, cfg_lock.write), but a file
    written before that -- or copied in by hand -- may still be 0644, and a
    system's config.local.toml holds its SMTP app password and Telegram bot
    token. So the hub and each master tighten what they find:
    config/*.local.toml and <systems dir>/*/config.local.toml. The app runs as
    the same account, so it still reads them.

    Never fails a start: a file this account may not chmod (another owner, a
    read-only mount) is skipped. Returns how many were tightened; the caller
    logs that number once, never a path's contents. POSIX only."""
    if os.name != "posix":
        return 0
    paths = list(root(config_dir).glob("*.local.toml"))
    for d in systems_dirs or ():
        try:
            paths.extend(pathlib.Path(d).glob("*/config.local.toml"))
        except OSError:
            continue
    done = 0
    for p in paths:
        try:
            st = os.lstat(p)
            if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o077:
                continue
            os.chmod(p, 0o600)
            done += 1
        except OSError:
            continue
    return done


def mask(value, keep=0):
    """Mask a secret for display. Never return a live secret from an API."""
    if not value:
        return ""
    return ("*" * 8) + (value[-keep:] if keep else "")
