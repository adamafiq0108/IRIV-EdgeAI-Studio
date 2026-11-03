# -*- coding: utf-8 -*-
"""
retention.py -- the hub-level data retention policy, and the sweep that
enforces it.

The policy is PLATFORM configuration, set by the admin on the hub (never
per-system), stored under [retention] in config/storage.toml with overrides in
storage.local.toml -- the same layering as every other hub setting:

    events_days    -- per-day event ARCHIVES on each system (the app keeps
                      ~7 days live and archives the rest; see
                      systems/*/app/store.py). Max 60, or 365 (below).
    snapshots_days -- evidence JPEGs under each system's data/snapshots. Max
                      60, or 365.
    clips_days     -- recorded MP4s under each system's data/clips. Max 60,
                      or 365.

The three day kinds are capped per system, by where that system's own data
and archive dirs live (system_tier): up to 365 days (12 months) on an NVMe
that is not removable and not on USB, 60 on the built-in SD/eMMC, a USB
drive, any other kind of drive, or anywhere that cannot be identified (a
system whose config cannot be read counts as eMMC). An NVMe fitted but not
holding the data does not count: the cap follows the data, not the hardware.

What the admin may CHOOSE is the box's cap (limits): 365 when any system's
data is on the NVMe, else 60 (Tok Aba 2026-10-05). A save above it is refused
in words. A SAVED value above it (saved when the data was on the NVMe, and the
drive is now not mounted) is KEPT as saved: what applies while the data is on
the built-in storage is 60 (policy, effective_policy), and the saved 365
applies again once the data is back on the NVMe. The admin is told once per
such spell (over_cap_notice: a flag, never a rewrite of the value). Data an
app saved on the built-in storage while its drive was missing is swept there
at 60 days too (sweep_system), so it cannot sit on the eMMC for ever. The
drive-not-mounted alarm itself is core/services/storage_watch.py.
    history_months -- the analytics day-files under <store>/history. Max 12.
    events_months  -- the master's Postgres event store. Max 12.

Auth, credentials, the audit log, configs, templates and workflows are NEVER
retention-managed. That is not a default -- it is structural: the sweep can
only ever delete a file that matches an explicit (directory, pattern, age)
allowlist, and a deny-list of load-bearing names is checked again right before
every unlink. A misconfigured policy can at worst delete media and archives
early; it cannot reach a credential file, because no allowlist entry can name
one.

There is deliberately NO "reset all data" here or anywhere else. The only
manual deletion is purge_system(): one named system, its media and event data
only, invoked explicitly by an admin.
"""

import datetime
import glob
import gzip
import os
import shutil
import threading
import time

from core import config as config_mod
from core.services import detector_control as dc

#: Upper bounds the admin can never exceed (product policy), on ANY box. The
#: day kinds only reach these on an NVMe; limits() is what this box allows.
LIMITS = {"events_days": 365, "snapshots_days": 365, "clips_days": 365,
          "history_months": 12, "events_months": 12}
#: The device-data kinds whose cap depends on the storage (storage_tier).
MEDIA_KEYS = ("events_days", "snapshots_days", "clips_days")
#: Their cap on a box whose data lives on the system disk (the SD/eMMC)...
BASE_MEDIA_DAYS = 60
#: ...and when a system's data and archive dirs are all on the NVMe.
EXTENDED_MEDIA_DAYS = 365
#: The page's names for the keys, so a refusal names the row the admin sees.
LABELS = {"events_days": "Event archives", "snapshots_days": "Snapshots",
          "clips_days": "Video clips", "history_months": "Analytics history",
          "events_months": "Event database"}
DEFAULTS = {"events_days": 45, "snapshots_days": 45, "clips_days": 45,
            "history_months": 12, "events_months": 12}

#: Files the sweep must never delete no matter how the policy is bent.
#: The LIVE event log and its rotation bookkeeping are protected here too:
#: retention prunes the ARCHIVE; the live window belongs to the app.
NEVER_DELETE = frozenset({
    "events.jsonl", "events.jsonl.1", "events.gen", "events.archived",
    "state.json", "layout.json",
    "config.toml", "config.local.toml", "storage.toml", "storage.local.toml",
    "users.local.toml", "secrets.local.toml", "audit.log", "audit.jsonl",
})

#: Extensions the sweep may remove, ever. A name outside this set is refused
#: even when an allowlist glob somehow matched it.
_DELETABLE_EXTS = (".jpg", ".jpeg", ".mp4", ".gz", ".jsonl")

#: How often the hub's background thread runs a full pass. Lives here, beside
#: the policy it enforces, so the panel can say WHEN the next one is rather
#: than leaving "it gets cleaned up eventually" as the whole answer.
SWEEP_INTERVAL_S = 6 * 3600

#: The last completed pass: {"at": epoch|None, "deleted": int}. Process-local
#: and deliberately not persisted -- it describes THIS hub process, and a
#: restart genuinely has not swept yet.
_last_sweep = {"at": None, "deleted": 0}


#: One month, in days, wherever a months-valued period becomes a date. Was 30
#: in the status report and 30.5 in the two sweeps that actually delete, so the
#: page quietly promised a cutoff half a day off what the sweep used.
MONTH_DAYS = 30.5


#: The systems' package dirs, as the last sweep (or route) saw them, so the
#: schedule shown without a list of systems still knows whose caps apply.
_known_pkg_dirs = []


def remember_pkg_dirs(pkg_dirs):
    """Record the systems the caps are worked out for (_known_pkg_dirs)."""
    _known_pkg_dirs[:] = [str(d) for d in (pkg_dirs or []) if d]
    return list(_known_pkg_dirs)


def data_dirs_of_system(pkg_dir):
    """A system's data dir and its event archive dir, or None when its config
    cannot be read -- which callers treat as "on the built-in storage" (the
    conservative 60), never as "skip this system"."""
    try:
        cfg = dc._merged(pkg_dir)
        data_dir = dc._data_dir(pkg_dir, cfg)
        out = [data_dir]
        arch = _archive_dir(pkg_dir, cfg, data_dir)
        if arch and arch not in out:
            out.append(arch)
        return out
    except Exception:                                        # noqa: BLE001
        return None


def _dev_of(path):
    """The block device a path lives on (st_dev), from its nearest existing
    ancestor -- a data dir not created yet still lives somewhere. None when
    nothing on the way up can be read."""
    p = os.path.abspath(str(path or os.sep))
    while True:
        try:
            return os.stat(p).st_dev
        except OSError:
            parent = os.path.dirname(p)
            if parent == p:
                return None
            p = parent


def _device_info(dev, sys_root="/sys"):
    """{"name", "removable", "usb"} for block device `dev` (an st_dev), read
    from sysfs; None where it cannot be read (no sysfs: a Windows dev box).

    The partition's sysfs node links to its disk; the DISK holds `removable`,
    and its resolved path says whether it hangs off a USB controller."""
    try:
        node = os.path.realpath(os.path.join(
            sys_root, "dev", "block", f"{os.major(dev)}:{os.minor(dev)}"))
    except (AttributeError, OSError, ValueError):
        return None
    if not os.path.isdir(node):
        return None
    disk = node
    if os.path.exists(os.path.join(node, "partition")):
        disk = os.path.dirname(node)
    try:
        with open(os.path.join(disk, "removable"), encoding="utf-8") as f:
            removable = f.read().strip() != "0"
    except OSError:
        removable = True                     # unknowable: not unlocking 365
    return {"name": os.path.basename(disk), "removable": removable,
            "usb": "/usb" in disk}


def _is_nvme(info):
    """An NVMe by its kernel name, fixed and not on USB. A fixed drive of any
    other kind (an SD card in a second slot can report itself non-removable)
    is not the storage this cap was promised for."""
    return bool(info) and info["name"].startswith("nvme") \
        and not info["removable"] and not info["usb"]


def _nvme_fitted(sys_root="/sys"):
    """Whether the box has an NVMe at all, wherever the data is. Only ever
    used for the WORDS: a fitted NVMe that does not hold the data unlocks
    nothing, but "move the data to it" is a different instruction from "fit
    one"."""
    return bool(glob.glob(os.path.join(sys_root, "block", "nvme*")))


def path_kind(path):
    """Where `path` lives: ("builtin" | "fixed" | "other" | "removable" |
    "unknown", a word for the device). Only an NVMe -- not removable, not on
    USB -- is storage that may hold a year of footage ("fixed"): a USB stick
    can be pulled and is not built for it, and the SD/eMMC is the disk the OS
    boots from, so both keep the 60-day cap. The root disk counts as the NVMe
    when it IS one (a box that boots from its NVMe keeps everything there)."""
    dev = _dev_of(path)
    root = _dev_of(os.sep)
    if dev is None or root is None:
        return "unknown", "storage that could not be identified"
    info = _device_info(dev)
    if dev == root:
        return (("fixed", "the NVMe") if _is_nvme(info)
                else ("builtin", "the built-in storage"))
    if info is None:
        return "unknown", "a drive that could not be identified"
    if info["removable"] or info["usb"]:
        return "removable", "a removable USB drive"
    if _is_nvme(info):
        return "fixed", "the NVMe"
    return "other", "a drive that is not an NVMe"


def cap_words(days):
    """A device-data cap as the page says it: 365 days is "12 months"."""
    return "12 months" if days >= EXTENDED_MEDIA_DAYS else f"{days} days"


def _longer(kind):
    """What would unlock 12 months, for data held to 60 on `kind` of storage.
    Said in the words of what to DO: an NVMe already fitted needs the data
    moved onto it, not another drive."""
    longer = cap_words(EXTENDED_MEDIA_DAYS)
    if kind == "builtin":
        return (" An NVMe drive is fitted, but the data is not on it: longer "
                f"retention needs the data on the NVMe drive (up to {longer})."
                if _nvme_fitted() else
                " Longer retention needs an NVMe drive with the data moved to "
                f"it (up to {longer}).")
    if kind == "removable":
        return (" Longer retention needs the data on a built-in NVMe drive "
                f"(up to {longer}).")
    if kind == "other":
        return f" Longer retention needs the data on an NVMe drive (up to {longer})."
    return ""


def dirs_tier(dirs, who="This system"):
    """The device-data cap for data kept in `dirs`, and why.

    {"extended", "limit_days", "reason", "kind"}: 365 only when EVERY dir is
    on the NVMe; anything on the built-in storage, a USB drive, another kind
    of drive or somewhere unidentifiable keeps the whole system at 60."""
    base = cap_words(BASE_MEDIA_DAYS)
    if dirs is None:
        return {"extended": False, "limit_days": BASE_MEDIA_DAYS,
                "kind": "unknown",
                "reason": (f"{who}'s settings could not be read, so its data "
                           f"is treated as on the built-in storage: up to "
                           f"{base}.")}
    kinds = [path_kind(d) for d in dirs] or [("builtin", "the built-in storage")]
    worst = next((k for k in kinds if k[0] != "fixed"), None)
    if worst is None:
        return {"extended": True, "limit_days": EXTENDED_MEDIA_DAYS,
                "kind": "fixed",
                "reason": (f"{who}'s data is on the NVMe: up to "
                           f"{cap_words(EXTENDED_MEDIA_DAYS)}.")}
    kind, label = worst
    bad_dir = next((d for d, k in zip(dirs, kinds) if k[0] != "fixed"), "")
    return {"extended": False, "limit_days": BASE_MEDIA_DAYS, "kind": kind,
            "label": label, "where": bad_dir,
            "reason": f"{who}'s data is on {label}: up to {base}.{_longer(kind)}"}


def system_tier(pkg_dir):
    """dirs_tier for ONE system, from where ITS data and archive dirs live."""
    return dirs_tier(data_dirs_of_system(pkg_dir),
                     who=os.path.basename(str(pkg_dir).rstrip("/\\")) or "This system")


def system_tiers(pkg_dirs=None):
    """{system slug: dirs_tier} for every system (default: the last seen)."""
    pkg_dirs = _known_pkg_dirs if pkg_dirs is None else pkg_dirs
    return {os.path.basename(str(p).rstrip("/\\")): system_tier(p)
            for p in pkg_dirs}


def _new_system_data_dir():
    """Where a system created now would keep its data, by the app's own rule
    (app/config.py data_dir): /mnt/nvme/cytron/<slug>/data when the NVMe is
    mounted, else the built-in storage. It used to be the STORE dir, which is
    where system PACKAGES live and can sit on the eMMC beside a mounted NVMe
    -- so a box with a drive fitted and no system yet said "data is not on the
    NVMe" (192.168.1.60, 2026-10-21)."""
    nvme = os.path.join(config_mod.NVME_MOUNT, "cytron", "new_system", "data")
    return nvme if os.path.ismount(config_mod.NVME_MOUNT) else str(config_mod.store_dir())


def storage_tier(config_dir=None, pkg_dirs=None):
    """The box-level summary for the page: the most any system may keep, the
    least, whether they differ, and one sentence -- the per-system detail is
    system_tiers(). With no systems yet, the place a system installed now
    would put its data stands in for them (_new_system_data_dir)."""
    tiers = system_tiers(pkg_dirs)
    if not tiers:
        tiers = {"": dirs_tier([_new_system_data_dir()], who="This box")}
    caps = [t["limit_days"] for t in tiers.values()]
    hi, lo = max(caps), min(caps)
    if len(tiers) == 1:
        reason = next(iter(tiers.values()))["reason"]
    elif hi == lo:
        reason = (f"Every system's data is on the NVMe: up to "
                  f"{cap_words(hi)}." if hi == EXTENDED_MEDIA_DAYS else
                  f"No system's data is on the NVMe: up to "
                  f"{cap_words(hi)}.{_longer('builtin')}")
    else:
        reason = ("Systems differ: each keeps device data according to the "
                  "drive its own data is on (see below).")
    # WHICH systems keep their data off the NVMe, and on what: the bell names
    # them, because "the data is not on the NVMe" with five systems and a drive
    # in the box is a sentence nobody can act on without knowing which one.
    off_nvme = [{"system": slug or "this box", "on": t.get("label", ""),
                 "where": t.get("where", "")}
                for slug, t in tiers.items() if not t["extended"]]
    return {"extended": hi == EXTENDED_MEDIA_DAYS, "limit_days": hi,
            "min_days": lo, "differs": hi != lo, "reason": reason,
            "off_nvme": off_nvme,
            # For the words only (the bell's notice offers "Configure Storage"
            # when there is a drive to move the data to).
            "nvme_fitted": _nvme_fitted()}


def limits(config_dir=None, pkg_dirs=None):
    """The most the admin may CHOOSE on this box: the device-data kinds up to
    the box's cap (365 when any system's data is on the NVMe, else 60), the
    months kinds up to 12. Where systems differ, what APPLIES to each is
    effective_policy() -- 365 chosen, 60 applied to the system on the eMMC."""
    cap = storage_tier(config_dir, pkg_dirs)["limit_days"]
    return {k: (min(v, cap) if k in MEDIA_KEYS else v)
            for k, v in LIMITS.items()}


def too_long(key, lim):
    """The refusal for a period above what this box allows, in the page's
    words: which row, the most it may be, and what would allow longer."""
    if key.endswith("_months"):
        return f"{LABELS[key]} can be kept for up to {lim[key]} months."
    if lim[key] >= EXTENDED_MEDIA_DAYS:
        return (f"{LABELS[key]} can be kept for up to 12 months "
                f"({lim[key]} days).")
    return (f"{LABELS[key]} can be kept for up to {lim[key]} days on this "
            "box: its data is not on the NVMe drive. Longer retention needs "
            "the data on the NVMe drive.")


#: The [retention] key that says the over-cap sentence has been shown for
#: the current spell. Not a period: set_policy never accepts it from a caller.
NOTICE_FLAG = "cap_notice_shown"


def saved_policy(config_dir=None):
    """The periods as SAVED, held only to the product maximums (LIMITS), never
    to what this box allows today: a 365 saved on the NVMe stays 365 while
    the drive is not mounted, and is what applies again when it is back."""
    raw = (config_mod.storage(config_dir) or {}).get("retention") or {}
    out = {}
    for key, default in DEFAULTS.items():
        try:
            value = int(float(raw.get(key, default)))
        except (TypeError, ValueError):
            value = default
        out[key] = max(1, min(LIMITS[key], value))
    return out


def _set_notice_flag(on, config_dir=None):
    """Set or clear NOTICE_FLAG, writing only when it changes."""
    local = config_mod.load_local("storage", config_dir)
    table = dict(local.get("retention") or {})
    if bool(table.get(NOTICE_FLAG)) == bool(on):
        return
    if on:
        table[NOTICE_FLAG] = True
    else:
        table.pop(NOTICE_FLAG, None)
    local["retention"] = table
    config_mod.save_local("storage", local, config_dir)


def over_cap_notice(config_dir=None, pkg_dirs=None):
    """One sentence, ONCE per spell, when a saved period is above what this
    box allows right now -- or [].

    The saved value is left exactly as it is (saved_policy): the drive that
    allowed it is usually only missing for a while. The flag is what makes
    it once: set when the sentence is given, cleared when nothing is over any
    more (the data back on the NVMe), so the next spell is said again."""
    lim = limits(config_dir, pkg_dirs)
    saved = saved_policy(config_dir)
    over = [k for k in MEDIA_KEYS if saved[k] > lim[k]]
    raw = (config_mod.storage(config_dir) or {}).get("retention") or {}
    if not over:
        if raw.get(NOTICE_FLAG):
            _set_notice_flag(False, config_dir)
        return []
    if raw.get(NOTICE_FLAG):
        return []
    _set_notice_flag(True, config_dir)
    names = [LABELS[k] for k in over]
    names = (names[0] if len(names) == 1
             else ", ".join(names[:-1]) + " and " + names[-1])
    return [f"{names}: kept for {lim[over[0]]} days while the data is on the "
            f"built-in storage. The saved {cap_words(saved[over[0]])} applies "
            "again when the data is back on the NVMe drive."]


def view(config_dir=None, pkg_dirs=None):
    """What the Retention page shows: the policy that APPLIES on this box (the
    fields), the policy as SAVED (where a row differs, the page says the
    saved one comes back with the NVMe), what this box allows choosing, the
    box summary, per system the cap that applies to it, why, and the periods
    that actually apply to its data -- plus the one-time sentence when a
    saved period is above the cap (notice) and the drive-not-mounted alarm
    (storage_watch.current)."""
    if pkg_dirs is not None:
        remember_pkg_dirs(pkg_dirs)
    notice = over_cap_notice(config_dir)
    pol = policy(config_dir)
    tiers = system_tiers()
    from core.services import storage_watch                  # noqa: PLC0415
    return {"policy": pol, "saved": saved_policy(config_dir),
            "limits": limits(config_dir), "notice": notice,
            "alarm": storage_watch.current(config_dir),
            "storage": storage_tier(config_dir),
            "systems": [{"slug": slug, "limit_days": t["limit_days"],
                         "reason": t["reason"], "kind": t["kind"],
                         "effective": {k: v for k, v in
                                       effective_policy(pol, t).items()
                                       if k in MEDIA_KEYS}}
                        for slug, t in tiers.items()]}


def effective_policy(pol, tier):
    """`pol` (the chosen policy) as it applies to data under `tier`."""
    out = dict(pol)
    for key in MEDIA_KEYS:
        out[key] = max(1, min(out[key], tier["limit_days"]))
    return out


def span_days(key, pol=None, config_dir=None):
    """A policy key as a number of DAYS, whatever unit it is stored in."""
    pol = pol or policy(config_dir)
    value = pol[key]
    return value * MONTH_DAYS if key.endswith("_months") else float(value)


def first_deletion_at(key, pol=None, config_dir=None):
    """When this kind first has anything old enough to delete.

    The retention clock starts when the policy came into force, not when the
    hub happened to boot: a 45-day rule that started today has nothing 45 days
    old under it yet, and deleting on the first pass would apply the new rule
    to data recorded under the old one. Nothing of this kind is removed until
    this moment passes; after it, deletion is by age as usual.

    Back-dating starts_at is therefore the way to enforce a shortened policy
    immediately -- a start already further back than the period makes this
    date past, and the next pass sweeps normally.
    """
    return starts_at(config_dir) + span_days(key, pol, config_dir) * 86400


def sweep_status(config_dir=None):
    """When the last pass ran, when the next one is due, when the policy came
    into force, and the two dates per kind that turn retention from a promise
    into something an operator can read off the screen.

    The dates are for the policy as it APPLIES, and where systems differ, as
    it applies to the most limited one: "the first clips go on 27 Sep" must
    not be a promise the 60-day system breaks."""
    tier = storage_tier(config_dir)
    pol = effective_policy(policy(config_dir),
                           {"limit_days": tier["min_days"]})
    last = _last_sweep["at"]
    now = time.time()
    return {"last_at": last, "last_deleted": _last_sweep["deleted"],
            "interval_s": SWEEP_INTERVAL_S,
            # When the policy currently in force STARTED. Persisted, unlike the
            # sweep bookkeeping above: "these numbers have applied since 3 Sep"
            # is a fact about the deployment, not about this process, and it
            # survives a restart because the policy does. Operator-settable --
            # a deployment that has been recording since before this box was
            # configured can say so.
            "starts_at": starts_at(config_dir),
            "starts_at_set": policy_set_at(config_dir) is not None,
            "next_at": (last + SWEEP_INTERVAL_S) if last else None,
            # Per kind: the moment the first data becomes deletable, and the
            # age cutoff applied once it has. Both epoch seconds; the page
            # renders them in local time.
            "first_deletion": {k: first_deletion_at(k, pol, config_dir)
                               for k in LIMITS},
            "cutoffs": {k: now - span_days(k, pol, config_dir) * 86400
                        for k in LIMITS},
            "due": {k: now >= first_deletion_at(k, pol, config_dir)
                    for k in LIMITS}}


def policy(config_dir=None):
    """The retention policy that APPLIES on this box today: saved_policy()
    held into [1, limits()], so neither a hand edit nor a 365 saved while the
    data was on the NVMe can stretch past what this box allows (the sweep
    reads this, so it honours the cap too). Read, never written back: the
    saved value is untouched and applies again when the data is back on the
    NVMe.

    Where systems differ, how long a system's device data is actually kept is
    effective_policy(policy, system_tier(that system)) -- 60 days for data on
    the built-in storage whatever was chosen."""
    cap = limits(config_dir)
    return {key: max(1, min(cap[key], value))
            for key, value in saved_policy(config_dir).items()}


def policy_set_at(config_dir=None):
    """Epoch the policy came into force, or None if nobody has set one.

    None is not a gap to fill with the install date -- a box running on the
    shipped defaults has genuinely never had a policy set. What it means for
    the sweep is decided in starts_at(), which is what the sweep actually
    reads."""
    raw = (config_mod.storage(config_dir) or {}).get("retention") or {}
    try:
        return int(raw.get("set_at")) or None
    except (TypeError, ValueError):
        return None


def starts_at(config_dir=None):
    """When the retention clock started, for the sweep's purposes.

    An unset policy reads as "since forever" (epoch 0) rather than as "now":
    a box nobody has configured is running the shipped defaults and has been
    since it was switched on, so its retention must behave exactly as it
    always has -- rolling, by age. Only a policy somebody SET starts a clock,
    and only then does the "nothing goes until the period has actually run"
    rule in first_deletion_at() apply.
    """
    return policy_set_at(config_dir) or 0


def set_starts_at(epoch, config_dir=None):
    """Move the date the policy counts from.

    Back-dating is the point: a deployment that has been recording for a year
    before anyone opened this page should not be told its first deletion is a
    year away. It is clamped to not be in the FUTURE, because a start date
    after today would push every deletion further out every time the page was
    opened, and there is no honest reading of "this rule takes effect next
    month" for a rule the sweep applies today.
    """
    try:
        epoch = int(float(epoch))
    except (TypeError, ValueError):
        raise ValueError("a start date must be a date")
    if epoch <= 0:
        raise ValueError("a start date must be a date")
    epoch = min(epoch, int(time.time()))
    local = config_mod.load_local("storage", config_dir)
    table = dict(local.get("retention") or {})
    table["set_at"] = epoch
    local["retention"] = table
    config_mod.save_local("storage", local, config_dir)
    return epoch


def set_policy(updates, config_dir=None, pkg_dirs=None):
    """Persist admin changes (unknown keys refused, a period above what this
    box allows refused in words) into storage.local.toml, key by key --
    explicit values, never popped keys."""
    unknown = sorted(set(updates or {}) - set(LIMITS))
    if unknown:
        raise ValueError(f"unknown retention keys: {', '.join(unknown)}")
    if pkg_dirs is not None:
        remember_pkg_dirs(pkg_dirs)
    local = config_mod.load_local("storage", config_dir)
    table = dict(local.get("retention") or {})
    # Refused, not clamped: a number typed past the cap is a request the box
    # cannot keep, and saving another number in its place would leave the
    # admin believing a promise nobody made. Every key is checked before the
    # file is written, so one refusal leaves the whole policy as it was.
    cap = limits(config_dir)
    for key, value in (updates or {}).items():
        try:
            value = int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{LABELS[key]} must be a number of "
                             f"{'months' if key.endswith('months') else 'days'}")
        if value > cap[key]:
            raise ValueError(too_long(key, cap))
        table[key] = max(1, value)
    # Stamped beside the values it belongs to. Not a key the API accepts (the
    # unknown-key check above runs on the CALLER's dict), so it cannot be
    # back-dated from a request.
    #
    # Stamped ONCE: the first save on a box starts the clock at today, and a
    # later save keeps whatever date is stored (product owner, 2026-10-11).
    # It used to re-stamp "now" on every save, so tweaking one period quietly
    # restarted every period's clock and put the next deletion a whole period
    # away. A date the admin PICKS is still applied, after this, by
    # set_starts_at (the route does that, clamped to today).
    if not policy_set_at(config_dir):
        table["set_at"] = int(time.time())
    local["retention"] = table
    config_mod.save_local("storage", local, config_dir)
    return policy(config_dir)


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

def _refuse(path):
    """The last line of defence, applied immediately before EVERY unlink."""
    name = os.path.basename(path).lower()
    if name in NEVER_DELETE:
        return f"protected file {name}"
    if not name.endswith(_DELETABLE_EXTS):
        return f"extension not deletable: {name}"
    if name.endswith(".jsonl") and not name.startswith(
            ("events-", "20")):        # archives + history day-files only
        return f"not an archive/history file: {name}"
    return None


def _unlink(path, report):
    veto = _refuse(path)
    if veto:
        report.setdefault("refused", []).append(f"{path}: {veto}")
        return False
    try:
        os.remove(path)
        report["deleted"] = report.get("deleted", 0) + 1
        return True
    except OSError as exc:
        report.setdefault("errors", []).append(f"{path}: {exc}")
        return False


def _day_from_name(name, prefix=""):
    """The YYYY-MM-DD a file name carries, or None."""
    stem = os.path.basename(name)
    if prefix:
        if not stem.startswith(prefix):
            return None
        stem = stem[len(prefix):]
    try:
        return datetime.date.fromisoformat(stem[:10])
    except ValueError:
        return None


def _archive_dir(pkg_dir, cfg, data_dir):
    override = ((cfg.get("storage") or {}).get("archive_dir") or "").strip()
    if override:
        return override if os.path.isabs(override) \
            else os.path.join(pkg_dir, override)
    return os.path.join(os.path.dirname(os.path.abspath(data_dir)), "archive")


def _sweep_files(folder, pattern, cutoff_day, report, by_mtime=False,
                 prefix=""):
    """Delete matches older than cutoff_day. Age comes from the filename's
    own date where the layout carries one (archives, history) -- the mtime of
    a copied/restored file lies -- and from mtime for media files, which are
    named by timestamp-id, not date."""
    for path in sorted(glob.glob(os.path.join(folder, pattern))):
        if by_mtime:
            try:
                day = datetime.date.fromtimestamp(os.path.getmtime(path))
            except OSError:
                continue
        else:
            day = _day_from_name(path, prefix)
            if day is None:
                continue                # unparseable names are left alone
        if day < cutoff_day:
            _unlink(path, report)


def _due(key, pol, now, config_dir=None):
    """Has this kind's retention period actually run since the policy started?

    Until it has, the rule has nothing old enough to act on: a 45-day policy
    set today would otherwise delete data recorded under the previous one on
    its very first pass. See first_deletion_at()."""
    return now >= first_deletion_at(key, pol, config_dir)


def sweep_system(pkg_dir, pol, now=None, report=None, config_dir=None):
    """Apply the policy to ONE system package: event archives, snapshots,
    clips. Never touches the live event log, labels, state or config."""
    report = report if report is not None else {}
    now = now or time.time()
    today = datetime.date.fromtimestamp(now)
    # A kind that is not due yet gets a cutoff older than any file can be, so
    # it matches nothing -- one date, rather than three conditionals around
    # three otherwise identical calls.
    never = datetime.date.min

    # THIS system's caps: where its own data lives (system_tier). A system on
    # the NVMe keeps what was chosen; one on the eMMC is held to 60 even if
    # another system on the box keeps a year.
    pol = effective_policy(pol, system_tier(pkg_dir))
    report.setdefault("effective", {})[os.path.basename(
        str(pkg_dir).rstrip("/\\"))] = {k: pol[k] for k in MEDIA_KEYS}

    def cut(key, days):
        return (today - datetime.timedelta(days=days)
                if _due(key, pol, now, config_dir) else never)
    cfg = dc._merged(pkg_dir)
    data_dir = dc._data_dir(pkg_dir, cfg)
    _sweep_files(_archive_dir(pkg_dir, cfg, data_dir), "events-*.jsonl.gz",
                 cut("events_days", pol["events_days"]),
                 report, prefix="events-")
    _sweep_files(os.path.join(data_dir, "snapshots"), "*.jpg",
                 cut("snapshots_days", pol["snapshots_days"]),
                 report, by_mtime=True)
    _sweep_files(os.path.join(data_dir, "clips"), "*.mp4",
                 cut("clips_days", pol["clips_days"]),
                 report, by_mtime=True)

    # The package's own ./data on the eMMC, when the data is somewhere else
    # now: where the app saved while its drive was not mounted (app/config.py
    # data_dir falls back there). The drive coming back does not move it, and
    # the pages read the drive again, so without this it would sit on the
    # system disk for ever. Held to the built-in storage's 60 days, by the
    # same allowlist as everything above.
    local = os.path.join(str(pkg_dir), "data")
    if os.path.isdir(local) and \
            os.path.realpath(local) != os.path.realpath(data_dir):
        lpol = effective_policy(pol, {"limit_days": BASE_MEDIA_DAYS})

        def lcut(key, days):
            return (today - datetime.timedelta(days=days)
                    if _due(key, lpol, now, config_dir) else never)
        _sweep_files(os.path.join(local, "snapshots"), "*.jpg",
                     lcut("snapshots_days", lpol["snapshots_days"]),
                     report, by_mtime=True)
        _sweep_files(os.path.join(local, "clips"), "*.mp4",
                     lcut("clips_days", lpol["clips_days"]),
                     report, by_mtime=True)
        local_arch = os.path.join(str(pkg_dir), "archive")
        if os.path.realpath(local_arch) != os.path.realpath(
                _archive_dir(pkg_dir, cfg, data_dir)):
            _sweep_files(local_arch, "events-*.jsonl.gz",
                         lcut("events_days", lpol["events_days"]),
                         report, prefix="events-")
    return report


def _sweep_history(pol, now, report):
    """History day-files: gzip past the in-memory window (the rings only ever
    read ~30 days back; see core/services/history.py), delete past
    history_months. Compression keeps 12 months affordable on an SD card --
    this data gzips ~10:1."""
    folder = str(config_mod.store_dir() / "history")
    today = datetime.date.fromtimestamp(now)
    # Not due yet: compress as usual (compression loses nothing), delete
    # nothing. datetime.date.min matches no file.
    keep_until = (today - datetime.timedelta(days=int(span_days("history_months", pol)))
                  if _due("history_months", pol, now) else datetime.date.min)
    compress_until = today - datetime.timedelta(days=35)
    for path in sorted(glob.glob(os.path.join(folder, "*.jsonl"))):
        day = _day_from_name(path)
        if day is None:
            continue
        if day < keep_until:
            _unlink(path, report)
        elif day < compress_until:
            try:
                with open(path, "rb") as src, \
                        gzip.open(path + ".gz.tmp", "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(path + ".gz.tmp", path + ".gz")
                _unlink(path, report)   # original, now that the .gz is safe
                report["compressed"] = report.get("compressed", 0) + 1
            except OSError as exc:
                report.setdefault("errors", []).append(f"{path}: {exc}")
    for path in sorted(glob.glob(os.path.join(folder, "*.jsonl.gz"))):
        day = _day_from_name(path)
        if day is not None and day < keep_until:
            _unlink(path, report)


def _sweep_db(pol, report, config_dir=None):
    """The master's Postgres event store, best-effort: an unreachable DB is a
    skipped cycle, never an error that stops the file sweep."""
    try:
        from core.repos import datasets as datasets_repo     # noqa: PLC0415
        from core.repos import events as events_repo         # noqa: PLC0415
        if not _due("events_months", pol, time.time(), config_dir):
            return
        cutoff = time.time() - span_days("events_months", pol) * 86400
        removed = events_repo.prune_before(cutoff, config_dir=config_dir)
        if removed:
            report["db_pruned"] = removed
        # Dataset rows are a projection of the same log and age with it: a
        # table that outlived the events it was built from would be history
        # nothing can be checked against.
        rows = datasets_repo.prune_before(cutoff, config_dir=config_dir)
        if rows:
            report["dataset_pruned"] = rows
        # Deleting a large slice leaves the planner believing the row counts
        # it measured before it -- which is how an index scan silently becomes
        # a sequential one. Only after something was actually removed.
        if removed or rows:
            report["analyzed"] = events_repo.analyze(config_dir=config_dir)
    except Exception as exc:                                 # noqa: BLE001
        report.setdefault("errors", []).append(f"postgres: {exc}")


def sweep(pkg_dirs, config_dir=None, now=None):
    """One full retention pass: every system's files, the history folder, the
    event database. Called from the hub's background timer and after a policy
    change; safe to call any time -- it only ever removes what the policy has
    aged out."""
    now = now or time.time()
    # Each system's own caps are worked out in sweep_system; remembered here
    # so the page's schedule speaks for the same systems.
    remember_pkg_dirs(pkg_dirs)
    pol = policy(config_dir)
    report = {"policy": pol}
    for pkg_dir in pkg_dirs or []:
        try:
            sweep_system(pkg_dir, pol, now, report, config_dir)
        except Exception as exc:                             # noqa: BLE001
            report.setdefault("errors", []).append(f"{pkg_dir}: {exc}")
    try:
        _sweep_history(pol, now, report)
    except Exception as exc:                                 # noqa: BLE001
        report.setdefault("errors", []).append(f"history: {exc}")
    _sweep_db(pol, report, config_dir)
    _last_sweep["at"] = now
    _last_sweep["deleted"] = report.get("deleted", 0)
    return report


# ---------------------------------------------------------------------------
# per-system manual delete (the ONLY manual deletion; no global reset exists)
# ---------------------------------------------------------------------------

#: What a manual purge may be narrowed to. "all" is the default everywhere so
#: an older caller that passes no scope keeps exactly today's behaviour.
PURGE_SCOPES = ("all", "events", "footage", "counters", "eventlog", "logs")

#: The process logs a system writes beside its data (the detection app's
#: app.log and its rotations, the fault spool the Logs page reads) and beside
#: its package (HailoRT's own log). The master's log file lives under the
#: repo's logs/ and is truncated rather than removed, because the master
#: holds it open for append and an unlinked file just keeps growing unseen.
#: app.log is the same case -- a running app holds it open "ab" as its
#: stdout -- so it is truncated too (APP_LOG_FILE) and is not in the globs.
#: logs_cleared.json is the Logs page's Clear Log mark into app.log
#: (core/services/log_collect.py CLEAR_MARK_FILE); with app.log emptied it
#: describes nothing, so it goes as well.
LOG_FILE_GLOBS = ("app.log.*", "faults.jsonl", "logs_cleared.json")
APP_LOG_FILE = "app.log"
PKG_LOG_GLOBS = ("hailort.log", "hailort.*.log")


# ---------------------------------------------------------------------------
# Counter reset, in the background
# ---------------------------------------------------------------------------
#
# Resetting counters stops the detection app, deletes three stores, bounces
# the system's master and starts detection again. That is seconds of work, and
# holding the request open for it means a button that looks frozen -- so the
# route starts a job and answers immediately, and the UI follows the status.
#
# Per SLUG, so two systems can reset at once and neither can be started twice.

_RESETS = {}
_reset_lock = threading.Lock()

#: How long a finished job stays readable. Long enough for a page that was
#: open to see it land, short enough that the dict cannot grow.
RESET_TTL_S = 300.0

#: The steps, in order, so the UI can show progress rather than a spinner.
RESET_STEPS = ("stopping detection", "clearing counters", "restarting system",
               "starting detection")


def reset_status(slug):
    """{state, step, error, removed, restarted} for one system's reset.

    state is "idle" | "running" | "done" | "failed". A caller polls this; a
    page that opens mid-reset reads "running" and can say so instead of
    rendering numbers that are about to change.
    """
    with _reset_lock:
        _reap_resets()
        state = _RESETS.get(str(slug))
        return dict(state) if state else {"state": "idle"}


def resetting_slugs():
    """Every system with a reset in flight -- for the hub's systems list, so a
    card can say "resetting" before someone clicks into it."""
    with _reset_lock:
        _reap_resets()
        return sorted(s for s, v in _RESETS.items() if v.get("state") == "running")


def _reap_resets(now=None):
    """Drop finished jobs past their TTL. Caller holds the lock."""
    now = time.time() if now is None else now
    for slug in [s for s, v in _RESETS.items()
                 if v.get("state") in ("done", "failed")
                 and now - float(v.get("finished") or 0) > RESET_TTL_S]:
        _RESETS.pop(slug, None)


def _reset_set(slug, **fields):
    with _reset_lock:
        _RESETS.setdefault(str(slug), {}).update(fields)


def start_counter_reset(systems_dir, slug, on_step=None, restart=None):
    """Kick off a counters reset in the background. Returns its first status.

    `restart` is called between the delete and the restart of detection; it is
    where the caller bounces the system's master (the hub owns that, this
    service does not). Anything it raises is reported, not swallowed: a reset
    whose master kept the old numbers is a reset that did not finish.
    """
    slug = str(slug or "").strip()
    with _reset_lock:
        if (_RESETS.get(slug) or {}).get("state") == "running":
            return dict(_RESETS[slug])
        _RESETS[slug] = {"state": "running", "step": RESET_STEPS[0],
                         "error": "", "removed": {}, "restarted": False,
                         "started": time.time(), "finished": 0}

    def _run():
        removed, restarted, was_running, app_up = {}, False, False, False
        try:
            try:
                st = dc.status(systems_dir, slug)
                was_running = bool(st.get("running"))
                app_up = bool(st.get("app_running") or was_running)
            except Exception:                                # noqa: BLE001
                was_running = app_up = False
            # STOP FIRST -- the whole app, not only its AI. The app holds its
            # tallies in memory and flushes them every few seconds, AI on or
            # off, so deleting the file under a running app just gets it
            # written back. Under the app lock, so the supervisor cannot
            # bring it back halfway through the delete.
            from core.services import app_lock                 # noqa: PLC0415
            from core.services import app_supervisor           # noqa: PLC0415
            with app_lock.held(slug):
                if app_up:
                    _reset_set(slug, step=RESET_STEPS[0])
                    dc.stop_app(systems_dir, slug)
                _reset_set(slug, step=RESET_STEPS[1])
                removed = purge_system(systems_dir, slug, "counters")
                _reset_set(slug, step=RESET_STEPS[2], removed=removed)
            if restart is not None:
                restarted = bool(restart())
            if was_running:
                _reset_set(slug, step=RESET_STEPS[3], restarted=restarted)
                # Back to what was running; a camera that dropped meanwhile
                # is the app's wait to keep, not this reset's error.
                dc.start(systems_dir, slug, require_camera=False)
            elif app_up:
                app_supervisor.reconcile(systems_dir, slug)
            _reset_set(slug, state="done", step="done", removed=removed,
                       restarted=restarted, finished=time.time())
        except Exception as exc:                             # noqa: BLE001
            _reset_set(slug, state="failed", error=str(exc) or
                       exc.__class__.__name__, removed=removed,
                       finished=time.time())
        if on_step:
            try:
                on_step(slug)
            except Exception:                                # noqa: BLE001
                pass

    threading.Thread(target=_run, name=f"reset-{slug}", daemon=True).start()
    return reset_status(slug)


def _remove_matching(pattern):
    """Remove every FILE the glob names; how many went. A folder or a name that
    will not go is skipped, not raised: every purge here is best effort across
    several stores, and one stubborn file must not fail the rest."""
    n = 0
    for path in glob.glob(pattern):
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            n += 1
        except OSError:
            pass
    return n


def purge_system(systems_dir, slug, scope="all"):
    """Delete ONE system's recorded data, on an admin's explicit request:
    live events, event archives, snapshots, clips. Configuration, zones,
    labels, credentials and state are untouched -- the system keeps running
    and keeps its setup; only its recorded history is gone.

    scope narrows WHAT is deleted, so an admin drowning in events can clear
    the event backlog without losing evidence they still want:

        "all"      -- events + archives + snapshots + clips (the default)
        "events"   -- the live event log and its rotation, plus the per-day
                      event archives. Snapshots and clips are left alone.
        "footage"  -- snapshots and clips only. Events and archives are left
                      alone.
        "counters" -- the NUMBERS on the dashboard: the per-day tallies the
                      counting rules keep, the event log they are derived
                      from, and this system's rows in the event store.
                      SNAPSHOTS AND CLIPS ARE KEPT -- that is the whole point
                      of it being separate from "all".

                      The event log has to go with them, however much it looks
                      like it should not: the Postgres rows are a PROJECTION
                      of that log, and the master re-reads it from offset 0
                      on every boot (core/services/local_ingest.py), so
                      deleting the rows alone means they come back at the next
                      restart and the numbers with them.

                      The caller stops detection first and starts it after --
                      the totals a rule holds since it started live in the
                      app's memory, it flushes its tallies every few seconds,
                      and deleting the file underneath a running app just gets
                      it written straight back.

        "eventlog" -- what the system's Events Logs page shows: this system's
                      rows in the event store, AND the live event log plus
                      its archives they are a projection of. The rows alone
                      would be back at the next master boot (see "counters"),
                      so the files go with them. Tallies, the operator's own
                      tables, snapshots and clips are kept, and nothing is
                      restarted -- this is "clear the event list", not a
                      reset.
        "logs"     -- what the system's System Logs page shows: the
                      detection app's app.log and rotations, the fault spool,
                      HailoRT's log beside the package, and the master's own
                      log file (truncated -- the master holds it open). The
                      master's in-memory ring is the ROUTE's to clear, over
                      HTTP as the operator, because it lives in another
                      process. No recorded data of any kind is touched.

    Scoped and deliberate by design. There is no all-systems variant, and
    none should ever be added."""
    if scope not in PURGE_SCOPES:
        raise ValueError("scope must be one of: " + ", ".join(PURGE_SCOPES))
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    cfg = dc._merged(pkg_dir)
    data_dir = dc._data_dir(pkg_dir, cfg)
    removed = {"events": 0, "archive": 0, "snapshots": 0, "clips": 0,
               "tallies": 0, "db_rows": 0, "dataset_rows": 0, "log_files": 0}
    if scope == "logs":
        for pattern in LOG_FILE_GLOBS:
            removed["log_files"] += _remove_matching(os.path.join(data_dir, pattern))
        for pattern in PKG_LOG_GLOBS:
            removed["log_files"] += _remove_matching(os.path.join(pkg_dir, pattern))
        # Emptied, NOT unlinked: deleting it under a running app left the app
        # writing into a file nobody could see until its next start. With the
        # app's O_APPEND fd the next line lands at the start of the file.
        app_log = os.path.join(data_dir, APP_LOG_FILE)
        try:
            if os.path.exists(app_log):
                with open(app_log, "r+b") as f:
                    f.truncate(0)
                removed["log_files"] += 1
        except OSError:
            pass
        # The master's log file: <repo>/logs/master-<slug>.log, where
        # provision.spawn_master opens it. Truncated in place, for the reason
        # in LOG_FILE_GLOBS' note.
        master_log = os.path.join(os.path.dirname(os.path.abspath(str(systems_dir))),
                                  "logs", f"master-{slug}.log")
        try:
            if os.path.exists(master_log):
                with open(master_log, "r+b") as f:
                    f.truncate(0)
                removed["log_files"] += 1
        except OSError:
            pass
        return removed
    if scope == "eventlog":
        try:
            from core.repos import events as events_repo     # noqa: PLC0415
            removed["db_rows"] = events_repo.delete_for_system(slug)
        except Exception:                                    # noqa: BLE001
            pass
        scope = "events"
    if scope == "counters":
        # The per-day tallies the counting rules keep (app/functions.py
        # DAILY_FILE): what "today / this week / this month" reads.
        tallies = os.path.join(data_dir, "fn_daily.json")
        try:
            if os.path.exists(tallies):
                os.remove(tallies)
                removed["tallies"] += 1
        except OSError:
            pass
        # This system's rows in the event store, so the Event Table and every
        # query widget start empty too. Best effort: a database that is down
        # must not fail the part of the reset that did work.
        try:
            from core.repos import events as events_repo     # noqa: PLC0415
            removed["db_rows"] = events_repo.delete_for_system(slug)
        except Exception:                                    # noqa: BLE001
            pass
        # The operator's own tables, which are a projection of the SAME log.
        # Clearing the log without clearing these would strand rows that no
        # replay can restore and no reset can reach.
        try:
            from core.repos import datasets as datasets_repo  # noqa: PLC0415
            removed["dataset_rows"] = datasets_repo.delete_for_system(slug)
        except Exception:                                    # noqa: BLE001
            pass
        # ... and the log those rows are a copy of. Without this the master
        # re-reads the log from the start on its next boot and puts every one
        # of them back -- the reset would look like it worked until something
        # restarted. Falls through to the shared event-clearing block below.
        scope = "events"
    if scope in ("all", "events"):
        for name in (("events.jsonl", "events.jsonl.1")):
            path = os.path.join(data_dir, name)
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed["events"] += 1
            except OSError:
                pass
        # events.gen / events.archived are deliberately KEPT: the generation
        # counter is what keeps cursors monotonic ("never reused") across the
        # purge -- deleting it would restart at 0 and hand old cursors a file
        # they mis-read. The app rotates onward from the surviving counter.
        # This holds for every scope, not just "all".
        for path in glob.glob(os.path.join(
                _archive_dir(pkg_dir, cfg, data_dir), "events-*.jsonl.gz")):
            try:
                os.remove(path)
                removed["archive"] += 1
            except OSError:
                pass
    if scope in ("all", "footage"):
        for sub, key, exts in (("snapshots", "snapshots", (".jpg", ".jpeg")),
                               ("clips", "clips", (".mp4", ".jpg"))):
            folder = os.path.join(data_dir, sub)
            for path in glob.glob(os.path.join(folder, "*")):
                if not path.lower().endswith(exts):
                    continue
                try:
                    os.remove(path)
                    removed[key] += 1
                except OSError:
                    pass
            # The thumbnails the grid draws from (detector_control keeps them
            # in a dot-folder beside the originals, which "*" does not match).
            # Not counted: they are copies of what was just counted, and a
            # folder of posters for footage nobody has is only disk.
            _remove_matching(os.path.join(folder, ".thumbs", "*.jpg"))
    return removed
