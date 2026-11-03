# -*- coding: utf-8 -*-
"""
store.py -- durable events and snapshots for the detection app.

Append-only JSONL plus a snapshot directory. Deliberately boring: the storage
choice is what decides whether the Contract's cursor rule can be satisfied at
all (docs/contract-v1.md sec 2).

**The cursor is an integer.** It is monotonic, never reused, never reordered,
and survives a restart because it is a property of the files rather than of a
counter in memory -- exactly what the contract demands. Concretely it encodes
(generation, byte offset):

    cursor = generation * GEN_STRIDE + byte offset into that generation's file

The generation starts at 0 and bumps by one each time events.jsonl rotates, so
a cursor written before rotation existed (a plain byte offset) is simply a
generation-0 cursor and keeps working. GEN_STRIDE is far larger than any file
the rotation cap allows, so offsets can never bleed into the generation part
and the cursor stays strictly monotonic across rotations.

**Rotation.** events.jsonl rotates -- renamed to events.jsonl.1, fresh file
started -- when EITHER rule trips, whichever comes first:

  * age: the oldest event in the live file is `live_days` old (default 7).
    This is the normal trigger: "save the live only for a week".
  * size: the live file passed `max_event_bytes` (default 64 MB). The
    BACKSTOP: age alone cannot protect a box where something goes wrong and
    writes a million rows in an hour, so runaway growth rotates early.

The previous generation is KEPT as events.jsonl.1 precisely so a reader that
is mid-file when rotation happens can finish it: read_since() serves the
remainder of events.jsonl.1 for a cursor one generation old, then rolls the
cursor into the current file at offset 0 -- no event skipped, none replayed.
A cursor two or more generations old names data that is no longer live; the
only honest answer is to restart from the top of what is live (the master's
event insert is idempotent on event id, so this dedupes downstream).

**Archive: rotated events are MOVED, never deleted.** After each rotation a
background thread copies events.jsonl.1 into the archive directory (default
<data_dir>/../archive -- the NVMe when data is there, the eMMC otherwise,
overridable with [storage].archive_dir), split into per-day gzip files:

    events-YYYY-MM-DD.<generation>.jsonl.gz

One file per (day, generation) rather than appending to one file per day,
because os.replace of a finished temp file is ATOMIC and IDEMPOTENT: a power
cut mid-archive redoes the whole generation and overwrites the same names --
no half-appended day file, no duplicated events, no lost ones. "The events of
a day" is simply every events-<day>.*.jsonl.gz concatenated. Gzip because the
data compresses ~10:1, the archive is write-once/read-rarely (export, audit),
and the read path is one stdlib module. `events.archived` records the last
generation safely archived; a rotation NEVER overwrites an unarchived .1 --
it defers (the live file simply keeps growing) until the archiver catches up,
because bounded growth is recoverable and a silently dropped event is not.
Deleting old archive files is deliberately NOT this module's job: the app
never deletes events; hub-level retention (admin-controlled) prunes archives.

Nothing here imports the dashboard, the agent, or the contract. The app writes;
the adapter reads.
"""

import datetime
import glob
import gzip
import json
import os
import shutil
import threading
import time

try:
    from . import faults
except ImportError:                     # run as a loose script, not a package
    import faults
try:
    from . import config as _app_config
except ImportError:
    try:
        import config as _app_config
    except ImportError:
        _app_config = None

log = faults.logger(__name__)

EVENTS_FILE = "events.jsonl"
EVENTS_PREV = "events.jsonl.1"
GEN_FILE = "events.gen"
ARCHIVED_FILE = "events.archived"
ARCHIVE_DIR = "archive"
SNAP_DIR = "snapshots"
#: Beside a snapshot's JPEG: <sid>.json (what it knows about itself) and
#: .crops/<sid>/<n>.jpg (a close-up per detected object). See app/snapmeta.py.
CROP_DIR = ".crops"

#: A snapshot held back until its timer settles is written as <id>.jpg.hold, so
#: nothing that lists the store's *.jpg sees it until it is released.
HOLD_EXT = ".hold"

#: Generation multiplier for the cursor. 2^40 bytes (1 TiB) per generation is
#: orders of magnitude beyond any rotation cap, so an offset can never collide
#: with the generation part.
GEN_STRIDE = 1 << 40

#: Size BACKSTOP for the live file (the age rule is the normal trigger).
DEFAULT_MAX_EVENT_BYTES = 64 * 1024 * 1024

#: How long events stay live before rotating toward the archive.
DEFAULT_LIVE_DAYS = 7.0


#: Snapshots are kept without a count cap. Two things still bound them: the
#: hub's age rule (retention.py, snapshots_days) and this free-space floor --
#: below it the oldest JPEGs go first so the drive never fills.
DEFAULT_MIN_FREE_MB = 1024

#: Every system made before the cap was lifted has `keep_snapshots = 500` in
#: its config.toml, written by the template rather than chosen by anyone. That
#: exact value reads as "no cap"; any other number is a deliberate choice.
LEGACY_KEEP_SNAPSHOTS = 500


def snapshot_cap(value):
    """The count cap for a config value: 0 means unlimited."""
    n = int(value or 0)
    return 0 if n == LEGACY_KEEP_SNAPSHOTS else max(0, n)


class Store:
    def __init__(self, data_dir, keep_snapshots=0, max_event_bytes=None,
                 live_days=None, archive_dir=None, min_free_mb=None):
        self.dir = data_dir
        self.events_path = os.path.join(data_dir, EVENTS_FILE)
        self.prev_path = os.path.join(data_dir, EVENTS_PREV)
        self.gen_path = os.path.join(data_dir, GEN_FILE)
        self.archived_path = os.path.join(data_dir, ARCHIVED_FILE)
        self.snap_dir = os.path.join(data_dir, SNAP_DIR)
        self.keep_snapshots = snapshot_cap(keep_snapshots)
        self.min_free_bytes = int(float(DEFAULT_MIN_FREE_MB if min_free_mb is None
                                        else min_free_mb) * 1024 * 1024)
        self.max_event_bytes = int(max_event_bytes or DEFAULT_MAX_EVENT_BYTES)
        self.live_days = float(DEFAULT_LIVE_DAYS if live_days is None
                               else live_days)
        # Beside the data dir by default: on the NVMe when the data is, on the
        # eMMC otherwise -- the archive degrades exactly like data_dir() does.
        # An explicit [storage].archive_dir on a drive that did not mount is
        # the empty mount point on the eMMC: never written into, the default
        # beside the data dir is used instead (app/config.py _unmounted_mount).
        if archive_dir and getattr(_app_config, "_unmounted_mount",
                                   lambda p: None)(archive_dir):
            log.warning("storage: archive dir %s is on a drive that is NOT "
                        "mounted; archiving beside the data dir instead",
                        archive_dir)
            archive_dir = None
        self.archive_dir = archive_dir or os.path.join(
            os.path.dirname(os.path.abspath(data_dir)) or data_dir, ARCHIVE_DIR)
        os.makedirs(self.snap_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._archiver = None           # background thread, at most one
        self._archive_error = None      # last failure, printed once per change
        self._live_since = None         # epoch of the live file's FIRST event
        self.generation = self._load_generation()
        # A .1 left unarchived by a crash/power cut is caught up on boot.
        self._kick_archiver()

    # -- generation bookkeeping ---------------------------------------------

    def _load_generation(self):
        """The persisted rotation count. Missing/garbage reads as 0, which is
        exactly right for a store that has never rotated (every existing
        deployment)."""
        try:
            with open(self.gen_path, "r", encoding="utf-8") as f:
                return max(0, int(f.read().strip() or 0))
        except (OSError, ValueError):
            return 0

    def _write_generation(self):
        tmp = self.gen_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(self.generation))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.gen_path)

    # -- archive bookkeeping ------------------------------------------------

    def _archived_generation(self):
        """The newest generation whose .1 file is safely in the archive."""
        try:
            with open(self.archived_path, "r", encoding="utf-8") as f:
                return int(f.read().strip() or -1)
        except (OSError, ValueError):
            return -1

    def _write_archived(self, gen):
        tmp = self.archived_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(gen))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.archived_path)

    def _kick_archiver(self):
        """Start the background archiver if there is work and none running."""
        if not os.path.exists(self.prev_path):
            return
        if self._archived_generation() >= self.generation - 1:
            return
        if self._archiver is not None and self._archiver.is_alive():
            return
        # once=True: this thread is MEANT to finish. Only a fault in it is
        # news -- an archive that stops running is how the live event file
        # grows until the disk fills.
        self._archiver = faults.spawn("event-archiver", self._archive_prev,
                                      once=True)
        self._archiver.start()

    def _archive_prev(self):
        """Copy events.jsonl.1 into per-day gzip archive files, then record
        the generation as archived. Runs on its own thread -- gzipping a
        week of events must never stall the detection loop -- and reads a
        file nothing writes to any more, so it needs no lock.

        Idempotent by construction: everything lands under a temp name and
        os.replace()s into events-<day>.<gen>.jsonl.gz. Interrupted anywhere
        before the marker write, the whole generation is redone and produces
        byte-identical files under the SAME names -- no event lost, none
        duplicated. The .1 file itself stays in place afterwards (readers one
        generation behind still need it); the NEXT rotation replaces it,
        which is safe exactly because the marker says it is archived.
        """
        gen_prev = self.generation - 1
        try:
            if self._archived_generation() >= gen_prev:
                return
            by_day = {}
            with open(self.prev_path, "r", encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        day = str(json.loads(raw).get("ts") or "")[:10]
                    except ValueError:
                        day = ""
                    if not (len(day) == 10 and day[4] == "-" and day[7] == "-"):
                        day = "unknown"
                    by_day.setdefault(day, []).append(raw)
            os.makedirs(self.archive_dir, exist_ok=True)
            for day, lines in sorted(by_day.items()):
                final = os.path.join(self.archive_dir,
                                     f"events-{day}.{gen_prev:06d}.jsonl.gz")
                tmp = final + ".tmp"
                with gzip.open(tmp, "wt", encoding="utf-8",
                               compresslevel=6) as out:
                    out.write("\n".join(lines) + "\n")
                os.replace(tmp, final)
            self._write_archived(gen_prev)
            if self._archive_error:
                self._archive_error = None
                log.info("archive: recovered; generation %s archived",
                         gen_prev)
        except OSError as exc:
            # Retention HALTS rather than dropping events: the live file keeps
            # growing (rotation defers on an unarchived .1) until this works.
            message = f"[APP] archive: FAILED for generation {gen_prev}: {exc}"
            if message != self._archive_error:
                self._archive_error = message
                log.error("%s - events are NOT being lost; the live log "
                          "grows until archiving succeeds", message,
                          exc_info=True)

    def _rotate_locked(self):
        """current -> .1 (replacing the ARCHIVED old .1), bump the generation.

        Refuses -- defers, really -- while the current .1 has not reached the
        archive yet: overwriting it would delete events, and this store never
        deletes events. The append that triggered rotation simply lands in
        the still-growing live file and the next append retries.

        Order matters for the cross-process reader (core's local_ingest):
        rename FIRST, then publish the new generation. In the window between
        the two, events.jsonl simply does not exist -- a reader answers empty
        and retries -- whereas publishing the generation first would point a
        catching-up reader at a .1 that is still the older archive.
        """
        if (os.path.exists(self.prev_path)
                and self._archived_generation() < self.generation - 1):
            self._kick_archiver()
            return
        try:
            os.replace(self.events_path, self.prev_path)
        except OSError:
            return                      # rotation is an optimisation, not a must
        self.generation += 1
        self._live_since = None
        try:
            self._write_generation()
        except OSError:
            pass
        self._kick_archiver()           # move the fresh .1 toward the archive

    # -- write (the app) ----------------------------------------------------

    def _first_event_epoch(self):
        """When the live file's FIRST event happened, as an epoch, so the age
        rule knows how old the file's content is. Survives restart because it
        is re-read from the file itself; falls back to the file's mtime for a
        first line that will not parse."""
        try:
            with open(self.events_path, "r", encoding="utf-8") as f:
                first = f.readline()
            ts = json.loads(first).get("ts")
            return datetime.datetime.fromisoformat(str(ts)).timestamp()
        except (OSError, ValueError, TypeError):
            try:
                return os.path.getmtime(self.events_path)
            except OSError:
                return None

    def _should_rotate(self, size, now):
        """Age (the week is up) or size (the runaway backstop), whichever
        trips first. In normal operation age fires first -- a week of
        debounced events is a few MB; under a runaway writer the size cap
        fires long before the week is up."""
        if self.max_event_bytes > 0 and size >= self.max_event_bytes:
            return True
        if self.live_days > 0 and size > 0:
            if self._live_since is None:
                self._live_since = self._first_event_epoch()
            if (self._live_since is not None
                    and now - self._live_since >= self.live_days * 86400.0):
                return True
        return False

    def append(self, event):
        """Append one event dict. Returns the new end cursor (int)."""
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                size = os.path.getsize(self.events_path)
            except OSError:
                size = 0                # no file yet -- nothing to rotate
            if size and self._should_rotate(size, time.time()):
                self._rotate_locked()
                size = 0
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())    # a power cut mid-write must not lose it
                if size == 0 and self._live_since is None:
                    self._live_since = time.time()
                return self.generation * GEN_STRIDE + f.tell()

    def save_snapshot(self, snapshot_id, jpeg_bytes, hold=False):
        """Write one JPEG and prune the oldest past keep_snapshots. `hold`
        writes it out of sight (release_snapshot shows it)."""
        if not jpeg_bytes:
            return None
        path = os.path.join(self.snap_dir,
                            f"{snapshot_id}.jpg" + (HOLD_EXT if hold else ""))
        with open(path, "wb") as f:
            f.write(jpeg_bytes)
        self._prune()
        return path

    def release_snapshot(self, snapshot_id):
        """Show a held snapshot. False when there was none held."""
        sid = os.path.basename(str(snapshot_id))
        try:
            os.replace(os.path.join(self.snap_dir, f"{sid}.jpg{HOLD_EXT}"),
                       os.path.join(self.snap_dir, f"{sid}.jpg"))
            return True
        except OSError:
            return False

    def release_held(self, older_than_s=0):
        """Show every held snapshot (older than `older_than_s` seconds when
        given). A stay that never ends cannot hold a photo back for ever, and
        a restart forgets what was waiting. Returns how many were shown."""
        shown = 0
        now = time.time()
        for path in glob.glob(os.path.join(self.snap_dir, "*.jpg" + HOLD_EXT)):
            try:
                if older_than_s and now - os.path.getmtime(path) < older_than_s:
                    continue
            except OSError:
                continue
            sid = os.path.basename(path)[:-len(".jpg" + HOLD_EXT)]
            shown += 1 if self.release_snapshot(sid) else 0
        return shown

    # -- a snapshot's record and close-ups (app/snapmeta.py) ------------------

    def _meta_path(self, snapshot_id):
        return os.path.join(self.snap_dir, f"{os.path.basename(str(snapshot_id))}.json")

    def save_snapshot_extra(self, snapshot_id, meta, crops=None):
        """Write the sidecar record and, when given, the close-up JPEGs
        ([bytes], in the order of meta["crops"]). Best effort: the picture is
        already saved and must never be lost to a failure here."""
        sid = os.path.basename(str(snapshot_id))
        try:
            if crops:
                folder = os.path.join(self.snap_dir, CROP_DIR, sid)
                os.makedirs(folder, exist_ok=True)
                for n, jpeg in enumerate(crops):
                    with open(os.path.join(folder, f"{n}.jpg"), "wb") as f:
                        f.write(jpeg)
            tmp = self._meta_path(sid) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, separators=(",", ":"))
            os.replace(tmp, self._meta_path(sid))
            return True
        except OSError:
            return False

    def snapshot_meta(self, snapshot_id):
        """The sidecar record, or None (an older snapshot, or none written)."""
        try:
            with open(self._meta_path(snapshot_id), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def update_snapshot_meta(self, snapshot_id, change):
        """Read, let `change(meta)` edit it (it returns True when it did), and
        write it back. Returns whether it was written."""
        meta = self.snapshot_meta(snapshot_id)
        if not meta or not change(meta):
            return False
        return self.save_snapshot_extra(snapshot_id, meta)

    def _drop_extras(self, snapshot_id):
        """Remove a snapshot's record and close-ups with the snapshot."""
        sid = os.path.basename(str(snapshot_id))
        try:
            os.remove(self._meta_path(sid))
        except OSError:
            pass
        shutil.rmtree(os.path.join(self.snap_dir, CROP_DIR, sid), ignore_errors=True)

    def _low_on_space(self):
        if self.min_free_bytes <= 0:
            return False
        try:
            return shutil.disk_usage(self.snap_dir).free < self.min_free_bytes
        except OSError:
            return False

    def _prune(self):
        """Oldest first: past the count cap when one is set, and whenever the
        drive is below its free-space floor (a few at a time, re-checked)."""
        capped = self.keep_snapshots > 0
        if not capped and not self._low_on_space():
            return
        files = sorted(glob.glob(os.path.join(self.snap_dir, "*.jpg")),
                       key=os.path.getmtime)
        if capped:
            for path in files[:max(0, len(files) - self.keep_snapshots)]:
                try:
                    os.remove(path)
                except OSError:
                    pass
                self._drop_extras(os.path.splitext(os.path.basename(path))[0])
            files = files[max(0, len(files) - self.keep_snapshots):]
        # Never delete the one just written (the last file).
        for i, path in enumerate(files[:-1]):
            if i % 20 == 0 and not self._low_on_space():
                break
            try:
                os.remove(path)
            except OSError:
                pass
            self._drop_extras(os.path.splitext(os.path.basename(path))[0])

    # -- read (the adapter) -------------------------------------------------

    def _read_file(self, path, offset, limit):
        """Whole lines from `path` starting at `offset`. Returns
        (events, end_offset, reached_eof)."""
        out = []
        eof = False
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            for _ in range(int(limit or 200)):
                line = f.readline()
                if not line:
                    eof = True
                    break
                if not line.endswith("\n"):
                    break               # partial write in flight; wait for it
                offset = f.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue            # skip a corrupt line, never crash
            else:
                # limit exhausted exactly at EOF still counts as EOF
                eof = f.tell() >= os.path.getsize(path)
        return out, offset, eof

    def read_since(self, cursor=None, limit=200):
        """Return (events, next_cursor).

        EXCLUSIVE: starts where the last batch ended, so the caller never sees
        the same event twice. No file yet, or nothing new, echoes the cursor
        back. A cursor one generation behind is served the rest of the
        previous file first; older than that has been retained away, so it
        restarts from the top of what is left rather than silently skipping.
        """
        gen, offset = self._decode(cursor)
        with self._lock:
            if cursor in (None, "", "0"):
                # From the very start of what is RETAINED: the previous
                # generation first when it still exists, so stats built by
                # walking the whole store cover both files.
                if os.path.exists(self.prev_path) and self.generation > 0:
                    gen, offset = self.generation - 1, 0
                else:
                    gen, offset = self.generation, 0

            if gen == self.generation - 1 and os.path.exists(self.prev_path):
                # Finish the rotated-out file. Rotation stopped all writes to
                # it, so EOF here is final and rolls into the current file.
                size = os.path.getsize(self.prev_path)
                if offset > size:
                    gen, offset = self.generation, 0
                else:
                    out, offset, eof = self._read_file(self.prev_path, offset, limit)
                    if eof:
                        return out, str(self.generation * GEN_STRIDE)
                    return out, str(gen * GEN_STRIDE + offset)

            if gen != self.generation:
                # Either ahead of us (a foreign cursor) or naming a generation
                # retention already deleted. Restart from the top of the
                # current file -- replaying retained history is the honest
                # option, and the master dedupes on event id.
                gen, offset = self.generation, 0

            if not os.path.exists(self.events_path):
                return [], str(gen * GEN_STRIDE + offset)
            size = os.path.getsize(self.events_path)
            if offset > size:
                # Truncated under us; a stale cursor must not read mid-line.
                offset = 0
            out, offset, _ = self._read_file(self.events_path, offset, limit)
            return out, str(gen * GEN_STRIDE + offset)

    def _decode(self, cursor):
        value = self._offset(cursor)
        return value // GEN_STRIDE, value % GEN_STRIDE

    @staticmethod
    def _offset(cursor):
        """Parse a cursor. Garbage raises ValueError -> the agent answers 4xx,
        which is what the master's health logic keys off."""
        if cursor in (None, "", "0"):
            return 0
        value = int(cursor)             # ValueError on garbage, deliberately
        if value < 0:
            raise ValueError(f"negative cursor: {cursor!r}")
        return value

    def all_events(self):
        # One read_since() call answers from ONE file; after a rotation the
        # retained history spans two, so follow the cursor until it dries up.
        events, cursor = [], None
        while True:
            batch, cursor = self.read_since(cursor, limit=10 ** 9)
            if not batch:
                return events
            events.extend(batch)

    def snapshot_bytes(self, snapshot_id):
        path = os.path.join(self.snap_dir, f"{os.path.basename(str(snapshot_id))}.jpg")
        if not os.path.exists(path):
            return None, None
        with open(path, "rb") as f:
            return f.read(), "image/jpeg"

    def snapshot_list(self):
        """[{id, ts, bytes}] newest first, from the files themselves."""
        rows = []
        for path in glob.glob(os.path.join(self.snap_dir, "*.jpg")):
            st = os.stat(path)
            rows.append({"id": os.path.splitext(os.path.basename(path))[0],
                         "mtime": st.st_mtime, "bytes": st.st_size})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return rows

    def delete_snapshots(self, snapshot_ids):
        removed = 0
        for sid in snapshot_ids or []:
            path = os.path.join(self.snap_dir, f"{os.path.basename(str(sid))}.jpg")
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
            self._drop_extras(sid)
        return removed
