# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
log_clear.py -- Clear Log: the mark that hides app.log's cleared lines, and
one system's purge. The hub clears its own ring only (Tok Aba, 2026-10-14):
it no longer asks every master to clear.

Lifted out of log_collect.py, which re-exports every name here (the Logs
route and the tests call them there), so that file stays under its line
ceiling. Nothing changed in the move. The collector's own names (its file
names, app_records) are read off log_collect at call time,
and the log lines keep its logger name.
"""

import json
import logging
import os
import time
import zlib

logger = logging.getLogger("core.services.log_collect")


#: Where Clear Log remembers how far into app.log it cleared. app.log itself
#: is never cut by Clear Log -- see clear_mark.
CLEAR_MARK_FILE = "logs_cleared.json"

#: How many bytes before the mark are fingerprinted. Enough that a file
#: refilled with different text past the same length is told apart.
CLEAR_MARK_PROBE = 256


def _probe(path, offset):
    """crc32 of the CLEAR_MARK_PROBE bytes ending at `offset`, or None."""
    try:
        with open(path, "rb") as handle:
            start = max(0, offset - CLEAR_MARK_PROBE)
            handle.seek(start)
            return zlib.crc32(handle.read(offset - start))
    except OSError:
        return None


def set_clear_mark(data_dir, app_log):
    """Record where app.log ended at the moment of a Clear Log."""
    try:
        offset = os.path.getsize(app_log)
    except OSError:
        offset = 0
    mark = {"ts": time.time(), "offset": offset,
            "crc": _probe(app_log, offset) if offset else 0}
    try:
        tmp = os.path.join(data_dir, CLEAR_MARK_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(mark, handle)
        os.replace(tmp, os.path.join(data_dir, CLEAR_MARK_FILE))
    except OSError as exc:
        logger.warning("[LOGS] could not record the clear mark: %s", exc)


def clear_mark(data_dir, app_log):
    """The byte offset in app.log above which Clear Log hid every line, or 0.

    WHY A MARK AND NOT A TRUNCATE. app.log is the app's whole stdout, the
    file somebody on SSH reads when the page is not enough; the Logs page
    only scrapes HailoRT's lines out of it. Clear Log empties the PAGE, so
    it leaves the file alone and remembers where it ended instead.

    A mark that no longer describes the file is IGNORED, never trusted: the
    app trims app.log in place past 16 MB (runtime.trim_log, same inode) and
    Settings' logs delete truncates it, after which the old offset points
    into unrelated text. So the mark also carries a crc of the bytes just
    before it, and both have to still match -- a file shorter than the mark
    or with different bytes there is a file that started again, with nothing
    left to hide.
    """
    try:
        with open(os.path.join(data_dir, CLEAR_MARK_FILE),
                  encoding="utf-8") as handle:
            mark = json.load(handle)
        offset = int(mark.get("offset") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0
    if offset <= 0:
        return 0
    try:
        if os.path.getsize(app_log) < offset:
            return 0
    except OSError:
        return 0
    return offset if _probe(app_log, offset) == mark.get("crc") else 0


def purge_app(systems_dir, slug):
    """Empty what this page reads of `slug`'s app. Returns how many page rows
    that was.

    THE SPOOL IS TRUNCATED IN PLACE, NEVER UNLINKED. The app opens it in
    append mode per record (systems/*/app/faults.py), so with O_APPEND the
    next record simply starts the file again. There is no lock to take: the
    writer takes none, and every installed system carries its own copy of
    that file, so one added here would bind nothing. A record written in the
    microseconds between the count and the truncate goes uncounted, which is
    honest enough for a trace line. The spool is the page's own store and
    nothing else reads it, so cutting it is right where app.log is not.

    APP.LOG IS NOT TOUCHED. Its HailoRT lines are rows on this page, so a
    mark records where it ended and the scrape skips everything above it
    (clear_mark). The file, its rotations, hailort.log and the master's log
    stay for whoever reads them over SSH; Settings' per-system "logs" delete
    is the way to remove those (core/services/retention.py).
    """
    from core.services import detector_control      # noqa: PLC0415 -- heavy
    from core.services import log_collect           # noqa: PLC0415 -- cycle
    try:
        data_dir = detector_control.data_dir(systems_dir, slug)
    except Exception as exc:                        # noqa: BLE001
        logger.debug("[LOGS] no data dir for %s: %s", slug, exc)
        return 0
    if not data_dir:
        return 0
    removed = len(log_collect.app_records(systems_dir, slug, limit=10 ** 6))
    spool = os.path.join(data_dir, log_collect.SPOOL_FILE)
    try:
        if os.path.exists(spool):
            with open(spool, "r+b") as handle:
                handle.truncate(0)
    except OSError as exc:
        logger.warning("[LOGS] could not clear %s: %s", spool, exc)
    try:
        # The spool's own rotation holds records too. Nothing has it open:
        # the app only ever renames onto it.
        os.remove(spool + ".1")
    except OSError:
        pass
    set_clear_mark(data_dir, os.path.join(data_dir, log_collect.APP_LOG_FILE))
    return removed

