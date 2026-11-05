# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
local_ingest.py -- feed the LOCAL detection app's events into analytics.

The sync loop pulls events from PAIRED devices over Contract v1 -- but a
standalone board runs its detection app right beside the master, with no agent
and no pairing. Without this, its detections never reach the event store, the
Analytics page, or the Event Table: the AI runs and the dashboard stays empty.

This tails the app's own append-only events.jsonl (byte-offset cursor, the same
idea as the device store's) each sync cycle, keeps running stats the widgets
bind to (events_today, violations_by_day, ...), and reads the live zone counts
from the app's state.json heartbeat. The master then treats the result exactly
like a synced device's: events extend the in-memory ring and persist to
Postgres; stats merge into the fleet aggregate.

Starting the cursor at 0 on master boot is deliberate: the in-memory ring is
empty after a restart, so re-reading the log repopulates it, and the Postgres
insert is idempotent on event_id so history never double-counts.
"""

import datetime
import json
import os

from core.services import detector_control
from core.services import main as main_service


class LocalIngest:
    """Stateful tail of one system's local event log. One per master process."""

    def __init__(self, systems_dir, slug, display_name=""):
        self.systems_dir = str(systems_dir)
        self.slug = slug
        self.display_name = display_name or slug
        self.offset = 0
        # Which rotation of events.jsonl the offset belongs to. The app bumps
        # data/events.gen when it rotates the log (events.jsonl ->
        # events.jsonl.1); tracking it lets this reader finish the rotated-out
        # file before moving to the fresh one, so rotation never skips events
        # for a reader mid-stream. A store that has never rotated has no gen
        # file, which reads as 0 -- the pre-rotation behaviour exactly.
        self.generation = 0
        # Running tallies, rebuilt from offset 0 at boot so they cover history.
        self.events_total = 0
        self.violations_total = 0
        self.events_by_day = {}
        self.violations_by_day = {}

    # -- where the app writes ------------------------------------------------

    def _data_dir(self):
        pkg_dir = os.path.join(self.systems_dir, self.slug)
        cfg = detector_control._merged(pkg_dir)
        return detector_control._data_dir(pkg_dir, cfg)

    # -- one poll ------------------------------------------------------------

    @staticmethod
    def _read_generation(data_dir):
        """The app's persisted rotation count (data/events.gen); 0 when the
        store has never rotated or predates rotation."""
        try:
            with open(os.path.join(data_dir, "events.gen"), "r",
                      encoding="utf-8") as f:
                return max(0, int(f.read().strip() or 0))
        except (OSError, ValueError):
            return 0

    def _read_lines(self, path, offset, limit):
        """Whole lines from `path` starting at `offset`.
        Returns (events, end_offset, reached_eof)."""
        events = []
        eof = False
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            for _ in range(limit):
                line = f.readline()
                if not line:
                    eof = True
                    break
                if line.endswith("\n"):
                    offset = f.tell()
                else:
                    break                       # partial write; retry next cycle
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            else:
                eof = f.tell() >= os.path.getsize(path)
        return events, offset, eof

    def poll(self, limit=2000):
        """New events since the last poll (parsed dicts), advancing the cursor.
        Missing file / no growth is a normal empty answer."""
        try:
            data_dir = self._data_dir()
        except Exception:                                   # noqa: BLE001
            return []
        path = os.path.join(data_dir, "events.jsonl")
        gen_now = self._read_generation(data_dir)
        was_gen, was_offset = self.generation, self.offset

        try:
            if gen_now == self.generation + 1:
                # The log rotated once since our last read: our offset points
                # into what is now events.jsonl.1. Finish it -- rotation
                # stopped all writes to it, so EOF there is final -- then move
                # to the fresh file at 0. Nothing skipped, nothing replayed.
                prev = os.path.join(data_dir, "events.jsonl.1")
                try:
                    prev_size = os.path.getsize(prev)
                except OSError:
                    prev_size = -1
                if prev_size < 0 or self.offset > prev_size:
                    # The archive is already gone (or unrecognisable): the
                    # unread remainder of that generation is lost to
                    # retention. Say so once, and resync to the current file.
                    print(f"[INGEST] {self.slug}: events log rotated past the "
                          "cursor; resyncing from the top of the current file")
                    self.generation, self.offset = gen_now, 0
                    events = []
                else:
                    events, self.offset, eof = self._read_lines(
                        prev, self.offset, limit)
                    if eof:
                        self.generation, self.offset = gen_now, 0
            elif (gen_now > 1 and self.generation == 0 and self.offset == 0
                  and os.path.exists(os.path.join(data_dir, "events.jsonl.1"))):
                # Master boot against a store that has already rotated: the
                # running tallies rebuild from history, and the retained
                # archive is part of it. Read it first; subsequent polls take
                # the gen_now == generation + 1 branch above until it is done.
                self.generation = gen_now - 1
                events, self.offset, eof = self._read_lines(
                    os.path.join(data_dir, "events.jsonl.1"), 0, limit)
                if eof:
                    self.generation, self.offset = gen_now, 0
            elif gen_now != self.generation:
                # First sight of this store (0 -> N at boot is normal), or we
                # fell 2+ rotations behind and the data between is retained
                # away. Rebuild from the top of the current file; the Postgres
                # insert is idempotent on event id, so replay never
                # double-counts.
                if self.generation:
                    print(f"[INGEST] {self.slug}: events log rotated "
                          f"{gen_now - self.generation}x since last read; "
                          "resyncing from the top of the current file")
                self.generation, self.offset = gen_now, 0
                events, self.offset, _ = self._read_lines(path, 0, limit)
            else:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    return []
                if size < self.offset:
                    self.offset = 0             # log truncated under us
                if size == self.offset:
                    return []
                events, self.offset, _ = self._read_lines(path, self.offset, limit)
        except OSError:
            # Mid-rotation window (events.jsonl briefly absent) or a transient
            # read failure: put the cursor back and answer empty -- the next
            # cycle sees a consistent generation and file.
            self.generation, self.offset = was_gen, was_offset
            return []

        if self._read_generation(data_dir) != gen_now:
            # The app rotated WHILE we were reading. The batch may straddle
            # the rename; discard it and rewind -- the same events are still
            # on disk (current file or events.jsonl.1) and the next poll reads
            # them from a consistent snapshot of gen + files.
            self.generation, self.offset = was_gen, was_offset
            return []

        for e in events:
            self._tally(e)
        return events

    def _tally(self, event):
        day = str(event.get("ts") or "")[:10]
        self.events_total += 1
        if day:
            self.events_by_day[day] = self.events_by_day.get(day, 0) + 1
        if event.get("type") == "violation":
            self.violations_total += 1
            if day:
                self.violations_by_day[day] = self.violations_by_day.get(day, 0) + 1
        # Bound the day buckets (~4 months) so a long-running master stays flat.
        for bucket in (self.events_by_day, self.violations_by_day):
            while len(bucket) > 120:
                bucket.pop(min(bucket))

    # -- stats the widgets bind to -------------------------------------------

    def stats(self):
        """The same flat keys a device adapter publishes, plus the app's LIVE
        zone counts from state.json -- so every existing widget key works for
        the local app too."""
        today = datetime.date.today().isoformat()
        out = {
            "events_total": self.events_total,
            "events_today": self.events_by_day.get(today, 0),
            "violations_total": self.violations_total,
            "violations_today": self.violations_by_day.get(today, 0),
            "events_by_day": dict(self.events_by_day),
            "violations_by_day": dict(self.violations_by_day),
        }
        try:
            state_path = os.path.join(self._data_dir(), "state.json")
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            counts = state.get("counts") or {}
            for key, value in counts.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[key] = value
                elif isinstance(value, str):
                    # A rule that picked a WORD publishes the word (the app's
                    # functions.py does this deliberately for an expression
                    # whose result is text, and outputs.py for an output's
                    # last error). Only numbers left this loop, so the app
                    # computed "People", wrote it to state.json, and every
                    # reader above -- the card, the canvas debug drawer, the
                    # variable picker -- showed 0 with nothing to explain it.
                    # Capped in one place (main.text_stat) because this value
                    # reaches every dashboard on a 4 second poll.
                    out[key] = main_service.text_stat(value)
                elif key.endswith("_by_day") and isinstance(value, dict):
                    # A counting function's per-day tally. Carried through as
                    # a bucket (same shape as violations_by_day), which is
                    # what makes Analytics' date range apply to it and a
                    # bucket chart able to render it.
                    out[key] = {str(d): n for d, n in value.items()
                                if isinstance(n, (int, float))
                                and not isinstance(n, bool)}
            # An app with the AI switched off (or an old logic-mode one) runs
            # no camera.
            out["camera_running"] = (bool(state.get("running"))
                                     and state.get("mode") != "logic"
                                     and not (state.get("mode") == "unified"
                                              and state.get("ai_on") is False))
            if state.get("camera"):
                # Masked here as well as by the app (Pipi H2): a state.json
                # written before 1.0.37 still holds the raw source, password
                # and all, and /api/main hands this to every account.
                out["camera_label"] = detector_control.mask_text(state["camera"])
            # Per-camera keys for multi-camera systems. A state.json written by
            # an older app has no `cameras`, and a single-camera system reads as
            # one -- so the count is never 0 and the singular keys above still
            # answer for every widget that predates this.
            cams = [c for c in (state.get("cameras") or []) if isinstance(c, dict)]
            out["camera_count"] = len(cams) or 1
            for cam in cams:
                cid = str(cam.get("id") or "").strip()
                fps = cam.get("fps")
                if cid and isinstance(fps, (int, float)) and not isinstance(fps, bool):
                    out[f"cam_{cid}_fps"] = fps
        except (OSError, ValueError):
            pass
        return out
