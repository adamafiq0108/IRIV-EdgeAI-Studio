# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
log_collect.py -- the records this process did not write, on the same page.

THE PROBLEM THIS EXISTS FOR. A board runs three kinds of process: the hub, one
master per system, and one detection app per running system. Only the first
two keep a log ring, and only the one being asked answers /api/logs -- so the
Logs page on a live board held exactly two loggers (core.services.masters and
cytron.access) while the same board's app.log carried

    [HailoRT] [error] CHECK failed - Lost communication with the server.

which is the failure the operator opened the page to find. Cameras, the
accelerator, detection and alert sending all live in the app process. The one
thing the page is for was the one thing it could not show.

HOW THE RECORDS TRAVEL, AND WHY THIS WAY.

The app writes its failures to a small JSON-lines spool in its own data dir
(systems/*/app/faults.py). The master READS that file when somebody asks it
for logs. Nothing is pushed, nothing is polled on a timer, and no process
holds another's memory.

EACH PAGE IS ITS OWN PROCESS (Tok Aba, 2026-10-14: "each system must show
logs for itself only ... the main (hub) log should show hub logs only"). A
system's page is its master plus its own app; the hub's page is the hub. The
hub used to ask every master and merge them all in, which put every system's
rows on the hub's page; that fan-out is gone, and own_sources() is the rule
the route enforces on whatever reaches it.

Three constraints decided it against the alternatives:

  * it must work with PostgreSQL down. A file involves no database, and the
    page an operator opens when the database is down is this one;
  * it must not write continuously to an SD card. The app spools WARNING and
    worse only, throttled per repeating failure, so a healthy board writes
    nothing here at all -- where an HTTP push would have cost a connection per
    record and a ring would have cost nothing until the moment it mattered;
  * a crashed process must leave its last records behind. This is the case the
    whole thing exists for: the app RE-EXECS ITSELF when the accelerator
    service drops it, and an in-memory ring polled over HTTP dies with the
    process holding the evidence. A push loses the same records whenever the
    master is the thing that restarted.

The cost is honest and worth naming: a record is visible only when someone
asks, and its lateness is bounded by the page's own refresh rather than being
instant. Nobody watches an idle Logs page for sub-second news.

WORDING IS THE CATALOG'S, NOT THE APP'S. The app cannot import core/ -- it is
spawned standalone on a bare Pi -- so it writes a catalog CODE plus its own
technical text. Expanding that code into the operator-facing sentence, the
category and the severity happens HERE, through core/services/log_catalog.py,
which is what stops the same failure reading one way from the app and another
way from the master.

NATIVE LINES. HailoRT prints its errors from C++ straight to fd 2, so they
never pass through Python logging and cannot be spooled. The tail of app.log
is scanned for those specific lines instead; they are the ones an operator is
actually chasing, and reading a bounded tail on request is far cheaper than
piping a detection app's stderr through this process.

No framework imports (docs/conventions.md sec 2): this takes app state and
returns dicts.
"""

import json
import logging
import os
import re
import sys
import threading
import time
import zlib

from core.services import log_catalog as catalog
from core.version import VERSION
# Clear Log lives in log_clear.py; re-exported for the route and the tests.
from core.services.log_clear import (CLEAR_MARK_FILE, CLEAR_MARK_PROBE,  # noqa: F401
                                     clear_mark, purge_app, set_clear_mark)

logger = logging.getLogger(__name__)

#: The file the app spools its failures to, inside the system's data dir.
SPOOL_FILE = "faults.jsonl"

#: The app's human log, scanned for the runtime's own native error lines.
APP_LOG_FILE = "app.log"

#: How much of app.log is read looking for native errors. The file is bounded
#: at 16 MB and rotates; reading the tail keeps this a fixed cost on a board
#: whose card is already busy serving video.
NATIVE_TAIL_BYTES = 64 * 1024

#: Most native INCIDENTS taken from one app.log, newest first. A runtime that
#: has gone wrong prints the same line thousands of times, and a page full of
#: one repeated line is a page with no other failure visible on it.
NATIVE_MAX = 25

#: How long a quiet spell has to be before the same failure printing again is
#: a NEW incident rather than the same one continuing.
#:
#: This was a byte distance for one afternoon, and it was wrong on a real
#: board: the gaps between nine HailoRT lines measured 654, 825, 1175, 2721,
#: 3655, 9558, 10473 and 30226 bytes, so a 2 KB rule split one repeatedly
#: dropping accelerator into six rows reading "2, 3, 1, 1, 1, 1". Byte
#: distance measures how CHATTY the app was in between, which is not the same
#: question as how long it was.
#:
#: Two minutes, against times read from app.log's own markers, which the app
#: writes once a minute (systems/*/app/faults.py STAMP_EVERY_S). A marker
#: dates everything below it to the minute, so anything finer would be
#: measuring the resolution rather than the fault.
#:
#: Lines that could NOT be dated -- nothing above them in the tail to date
#: from -- are never split: they collapse into one row per event code per
#: file. A file that cannot date its lines must not have the page imply six
#: separate instants on its behalf.
NATIVE_INCIDENT_S = 120.0

#: Lines the accelerator runtime prints natively, and what they mean. Ordered:
#: the first match wins, so the specific service-drop test sits above the
#: catch-all for any other HailoRT error.
NATIVE_RULES = (
    (re.compile(r"COMMUNICATION_CLOSED|Lost communication with the server",
                re.I), "AI_SERVICE_CRASHED"),
    (re.compile(r"OUT_OF_PHYSICAL_DEVICES|status=74"), "AI_HAILO_BUSY"),
    (re.compile(r"\[HailoRT\]\s*\[error\]"), "AI_SERVICE_UNAVAILABLE"),
)

#: The line runtime.start() writes into app.log when a run begins. app.log is
#: APPEND-ONLY ACROSS RUNS -- it is the app's stdout, reopened "ab" every
#: start -- so a board started and stopped for months has every run's output
#: in one file with nothing between them. Reading backwards, the first marker
#: reached is where the current run began, and everything past it belongs to a
#: run that ended.
#:
#: Tolerant of the number of "=" so an older app package's line still counts.
NATIVE_START_RE = re.compile(r"^\[APP\] =+ app started\b")

#: The time markers app.log carries, and the only real clock a scraped line
#: can be given. The app writes one a minute while it is logging anything
#: (systems/*/app/faults.py), and the run marker above carries a time too --
#: one pattern reads both, so a run that has only just started still dates its
#: lines from its first line.
#:
#: Local time, because that is what the app wrote with time.strftime and both
#: processes are on the same board. A log copied off the board to a machine in
#: another timezone reads an hour or two out; the alternative is changing what
#: an operator sees in the file, which is the thing this deliberately does not
#: do.
NATIVE_TIME_RE = re.compile(
    r"^\[APP\] =+ (?:app started )?"
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\b")

#: How stale app.log may be and still be worth scraping.
#:
#: The scraped rows are stamped with the FILE's mtime, so a log nobody has
#: written to since September produces rows dated September -- accurate, and
#: still the wrong thing to put in front of somebody chasing a fault today.
#: Two systems on one board carried 26,240 and 4,155 HailoRT lines in logs
#: last touched three weeks earlier.
#:
#: A DAY, not an hour: the classic support case is "it died overnight and I
#: found it this morning", and those lines are the ones the scan exists for.
#: A file older than that is history, and history is what the spool keeps --
#: it carries real timestamps and survives the crash.
NATIVE_STALE_S = 24 * 3600

#: The `run` token a scraped line carries. NOT a process run: nothing wrote
#: these, and the empty string that used to sit here said so honestly -- but
#: it also left the page keying them on `ts|message`, and every poll re-reads
#: the same 64KB and produces the same lines again. One fixed word, with a
#: content-and-position `seq` beside it, makes the same scraped line the same
#: row on every poll while still being unmistakably not a process's stamp.
NATIVE_RUN = "scan"

#: How far apart a scraped line and a real record may be and still be one
#: event. Still wide: a scraped line is dated to the MINUTE at best (from the
#: nearest marker) and falls back to app.log's MTIME -- the last write to the
#: whole file, not the time of that line -- when there is no marker, so it
#: reads as "now"
#: while the record it duplicates is however long ago the failure was. That is
#: what meta.approx_time is announcing, and a tight window here would let the
#: same HailoRT drop appear twice, once spooled and once scraped.
NATIVE_DUP_WINDOW_S = 15 * 60

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# ---------------------------------------------------------------------------
# Where a record came from
# ---------------------------------------------------------------------------

def local_source(state):
    """What THIS process calls itself on a record: "hub" or "master:<slug>".

    An operator should never have to know which process noticed a failure --
    that is the product's problem -- but "which system" is a question they ask
    constantly, and a board runs four of them.
    """
    system = getattr(state, "system", None)
    slug = getattr(system, "slug", None)
    return f"master:{slug}" if slug else "hub"


def app_source(slug):
    return f"app:{slug}"


def own_sources(state):
    """The sources a page from THIS process may show: the hub's page "hub"
    only; a system's page its master and its own app, never the hub and
    never another system."""
    system = getattr(state, "system", None)
    slug = getattr(system, "slug", None)
    if slug:
        return {f"master:{slug}", app_source(slug)}
    return {"hub"}


def _tag(entries, src):
    """Stamp records that do not already carry a source.

    A record that already names its process keeps that name: an app row is
    app:<slug>, not master:<slug>, or the page would say the master reported
    a camera the master never saw.

    The key is `source`. It briefly was `src` here while `source` still held
    "module:lineno" on a ring record; that spelling is gone (module, file,
    function and line are their own fields), so the two halves use one name
    and the page stopped having to guess which meaning it was looking at.
    """
    for entry in entries or ():
        if not entry.get("source"):
            entry["source"] = src
    return entries or []


# ---------------------------------------------------------------------------
# The app's spool
# ---------------------------------------------------------------------------

def _level_at_least(level, wanted):
    if wanted not in LEVELS:
        return True
    try:
        return LEVELS.index(level) >= LEVELS.index(wanted)
    except ValueError:
        return True


def _expand(raw, src):
    """One spooled app record as a row of the Logs page.

    The app wrote facts and a code. The catalog owns the sentence, the
    category and the severity, so all three are filled in here -- a record
    that arrives with a code is levelled by the CATALOG, not by whatever the
    call site felt like at the time, which is how the same fault ends up
    reading identically wherever it was noticed.
    """
    code = str(raw.get("event_code") or "")
    entry = catalog.get(code)
    text = str(raw.get("message") or "")
    module = str(raw.get("module") or "")
    line = raw.get("line") or 0
    if not module:
        # A spool written by an app package that has not been redeployed yet.
        # Every installed system keeps its OWN copy of app/, so the release
        # tree moving forward does not move theirs -- and the older copy puts
        # "module:lineno" in `source`, which on this record now means the
        # process that noticed the failure. Read the old shape rather than
        # leaving a board's rows blank until somebody reinstalls it.
        module, _, line = str(raw.get("source") or "").partition(":")
    level = str(raw.get("level") or "ERROR").upper()
    if entry is not None:
        level = entry.severity
    row = {
        "ts": float(raw.get("ts") or 0.0),
        # Stamped by the process that WROTE the record, never invented here:
        # a fabricated sequence number is one the page would dedupe on, and
        # the rows it dropped would be the ones nobody could explain. A
        # writer that does not stamp them arrives empty, on purpose.
        "seq": int(raw.get("seq") or 0),
        "run": str(raw.get("run") or ""),
        "level": level if level in LEVELS else "ERROR",
        "logger": str(raw.get("logger") or "app"),
        # The catalog's wording when there is a code; the app's own sentence
        # when there is not, because an uncatalogued line is still better on
        # the page than a blank one.
        "message": (entry.message if entry is not None else text)[:2000],
        "event_id": entry.event_id if entry is not None else "",
        "event_code": entry.code if entry is not None else "",
        "category": entry.category if entry is not None else "",
        "status": "failed" if (level in ("ERROR", "CRITICAL")) else "success",
        "module": module or "app",
        "function": "",
        "file": f"app/{module}.py" if module else "",
        "line": int(line) if str(line).isdigit() else 0,
        "user": "",
        "session": "",
        # The reading process's version. The app is the same release tree on
        # this board, and a row with no version at all is a support ticket
        # nobody can reproduce.
        "version": VERSION,
        "request_id": "",
        "action": entry.action if entry is not None else "",
        # The technical text always survives: it is the only string that ever
        # actually diagnoses anything.
        "detail": text[:4000],
        "meta": raw.get("meta") or {},
        "source": src,
    }
    if raw.get("traceback"):
        row["traceback"] = str(raw["traceback"])[:8000]
    return row


def read_spool(path, src, level=None, contains=None, since=None, limit=200):
    """The app's spooled failures as page rows, newest first.

    Never raises: a system whose app has never run has no spool, and that is a
    normal state, not an error on somebody else's page.
    """
    lines = _tail_lines(path, NATIVE_TAIL_BYTES * 4)
    out = []
    for text in reversed(lines):
        text = text.strip()
        if not text.startswith("{"):
            continue
        try:
            raw = json.loads(text)
        except ValueError:
            continue                    # a half-written last line; skip it
        row = _expand(raw, src)
        if not _keep(row, level, contains, since):
            continue
        out.append(row)
        if len(out) >= int(limit or 200):
            break
    return out


def _keep(row, level=None, contains=None, since=None):
    if level and not _level_at_least(row["level"], str(level).upper()):
        return False
    if since and row["ts"] < float(since):
        return False
    if contains:
        needle = str(contains).lower()
        hay = " ".join((row.get("message", ""), row.get("event_id", ""),
                        row.get("detail", ""))).lower()
        if needle not in hay:
            return False
    return True


def _tail_indexed(path, size):
    """The last `size` bytes of a file as (offset, text), offset from the
    file's START. [] if unreadable.

    The offset is what makes a scraped line identifiable. Two polls of a file
    nobody has rotated see the same line at the same byte, so "the line at
    412_336 saying X" is the same row both times -- where the text alone is
    not, a runtime in trouble printing the identical sentence a thousand
    times over.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            start = max(0, handle.tell() - int(size))
            handle.seek(start)
            blob = handle.read()
    except OSError:
        return []
    if start:
        # The first line of a mid-file read is almost always half a line.
        head, sep, blob = blob.partition(b"\n")
        start += len(head) + len(sep)
    out = []
    # Split on \n only, never splitlines(): the offset has to count the bytes
    # the file actually holds, and splitlines() also breaks on \r and \x85,
    # which would slide every following offset by one.
    for raw in blob.split(b"\n"):
        out.append((start, raw.decode("utf-8", "replace")))
        start += len(raw) + 1
    return out


def _tail_lines(path, size):
    """The last `size` bytes of a file, as decoded lines. [] if unreadable."""
    return [text for _, text in _tail_indexed(path, size)]


def _native_seq(offset, text):
    """A stable id for one scraped line: where it sits, and what it says.

    Not a counter -- nothing counted these -- so it is a checksum of the two
    facts that identify the line. Never 0: the page reads seq 0 as "this
    writer does not stamp records" and falls back to keying on ts+message,
    which is the duplicate-rows bug this exists to close.
    """
    return zlib.crc32(f"{offset}|{text}".encode("utf-8", "replace")) or 1


def native_records(app_log, src, level=None, contains=None, since=None,
                   limit=NATIVE_MAX, started_at=0, floor=0):
    """Accelerator-runtime errors from the tail of app.log.

    These are printed by HailoRT's C++ from fd 2, so they never reach Python
    logging and cannot be spooled -- and they are exactly the lines an
    operator is chasing ("Lost communication with the server").

    ONE INCIDENT IS ONE ROW. HailoRT does not print a failure, it prints a
    paragraph: a single service drop put three lines in app.log --

        [HailoRT] CHECK failed - Lost communication with the server.
        [HailoRT] CHECK_SUCCESS failed with status=HAILO_COMMUNICATION_CLOSED
        [HailoRT] Failed to send request, status = HAILO_COMMUNICATION_CLOSED

    -- which all mean AI_SERVICE_CRASHED, so the page drew three rows with the
    same second, the same event id and the same catalog sentence. To an
    operator that is one failure reported three times, and it is what they
    opened a ticket about. Lines of the same kind are folded into one row that
    KEEPS THEM ALL: the sentence on the row, every line in `detail`, the tally
    in `meta.repeats`.

    WHAT SPLITS TWO INCIDENTS IS TIME, and only time it can actually read.
    The app writes a marker line into app.log once a minute, and a scraped
    line is dated by the nearest marker ABOVE it -- a real time, not the
    file's mtime. Same code, more than NATIVE_INCIDENT_S apart, is a second
    incident and its own row; a board whose accelerator drops three times in
    an hour says so.

    WHEN NOTHING CAN DATE THEM, ONE ROW PER CODE. An app package that predates
    the markers, or a tail that begins below the last one, leaves lines whose
    only possible timestamp is the file's mtime -- one value for the whole
    file. Splitting those would assert several distinct instants the file
    cannot support, which is exactly how six rows came to claim 16:02:43. They
    collapse into one row per code instead, with `meta.approx_time` true,
    which is the honest reading of a file that cannot tell the time.

    BOUNDED TO THE CURRENT RUN, two ways, because the file outlives the
    process that wrote it:

      * WHERE -- reading stops at the last run marker (NATIVE_START_RE). Start
        a system whose app.log holds 26,000 HailoRT lines from a run that died
        in September and, without this, the first poll scrapes September's
        failure and dates it now;
      * WHETHER -- a file nobody has written to for a day is not scraped at
        all, and neither is one older than the run that is supposedly writing
        it (`started_at`). Both are the stale-banner rule: a stopped system
        must not carry a live fault.

    A file with NO marker and a recent mtime is still scraped in full. That is
    the case this whole function exists for -- an app package nobody has
    redeployed yet writes no marker -- and losing it would cost more than the
    staleness it prevents.
    """
    try:
        when = os.path.getmtime(app_log)
    except OSError:
        return []
    if time.time() - when > NATIVE_STALE_S:
        return []
    if started_at and when < float(started_at):
        # The run writing this log has not written to it yet, so everything in
        # it belongs to a run that has ended.
        return []
    if since and when < float(since):
        return []
    found = _dated_lines(app_log)
    if floor:
        # Clear Log's mark (see clear_mark): lines above it were cleared.
        found = [item for item in found if item["offset"] >= floor]
    rows = [_incident_row(incident, when, src)
            for incident in _incidents(found)]
    # Newest incident first, which for a dated one is its own time and for an
    # undated one is the file's. merge() sorts the whole page again; this is
    # so the cap below keeps the NEWEST rows rather than the first ones found.
    rows.sort(key=lambda r: float(r.get("ts") or 0.0), reverse=True)
    rows = rows[:int(limit or NATIVE_MAX)]
    return [row for row in rows if _keep(row, level, contains, since)]


def _dated_lines(app_log):
    """Every native error line in the tail, in FILE ORDER, with its time.

    Time comes from the nearest marker ABOVE the line, which means reading
    backwards and holding the lines seen so far until a marker turns up to
    date them -- markers are written once a minute, so a handful of lines wait
    at most. A line with no marker above it inside the tail keeps None, and
    the row built from it says so.

    Stops at the run marker: everything above it was printed by a process that
    has exited (see the module docstring on app.log being append-only).
    """
    found = []
    undated = []
    for offset, text in reversed(_tail_indexed(app_log, NATIVE_TAIL_BYTES)):
        text = text.strip()
        if not text:
            continue
        match = NATIVE_TIME_RE.match(text)
        if match is not None:
            stamp = _marker_time(match.group(1))
            if stamp is not None:
                for item in undated:
                    item["when"] = stamp
                undated = []
        if NATIVE_START_RE.match(text):
            # Dated first, THEN stop: the run marker carries a time of its
            # own, so the first lines of a run are datable from it alone.
            break
        if match is not None:
            continue
        code = next((c for pattern, c in NATIVE_RULES if pattern.search(text)),
                    None)
        if code is None:
            continue
        item = {"offset": offset, "text": text, "code": code, "when": None}
        found.append(item)
        undated.append(item)
    return found[::-1]


def _marker_time(text):
    """A marker's "YYYY-MM-DD HH:MM:SS" as an epoch, or None if it will not
    parse. Local time: see NATIVE_TIME_RE."""
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _incidents(found):
    """Dated lines grouped into incidents, oldest first.

    Per EVENT CODE, so a service drop and a busy chip in the same minute stay
    two rows: they are two different things to do something about. Within a
    code, a gap of more than NATIVE_INCIDENT_S starts a new incident -- and
    only when BOTH sides of the gap are dated, because an undated line cannot
    be shown to be far from anything.
    """
    incidents = []
    open_by_code = {}
    for item in found:
        when = item["when"]
        incident = open_by_code.get(item["code"])
        if incident is not None and when is not None \
                and incident["last"] is not None \
                and when - incident["last"] > NATIVE_INCIDENT_S:
            incident = None
        if incident is None:
            incident = {"code": item["code"], "lines": {}, "count": 0,
                        # The EARLIEST line anchors the row's identity, so an
                        # incident still being printed into keeps the same key
                        # poll after poll: anchoring on the newest line would
                        # rename the row every time HailoRT printed again,
                        # which is the duplicate-rows bug wearing a hat.
                        "anchor": (item["offset"], item["text"]),
                        "first": when, "last": when}
            incidents.append(incident)
            open_by_code[item["code"]] = incident
        if when is not None:
            incident["last"] = when
            if incident["first"] is None:
                incident["first"] = when
        incident["lines"][item["text"]] = \
            incident["lines"].get(item["text"], 0) + 1
        incident["count"] += 1
    return incidents


def _incident_row(incident, file_when, src):
    """One folded incident as a page row.

    The row shows the CATALOG SENTENCE and nothing technical -- one failure,
    one line, in the words an operator can act on. Every printed line is kept
    in `detail`, oldest first, which is what the details panel is for; a line
    the runtime printed more than once carries its own count rather than being
    listed twice.

    THE TIME is the incident's LAST dated line: what the row reports is a
    failure that was still printing then, and on a page sorted newest-first
    that is where a reader looks for it. `meta.span_s` carries how long it ran
    for, which is the fact the start time would have added. Only when nothing
    in the incident could be dated does the file's mtime stand in, and then
    `meta.approx_time` says so.
    """
    entry = catalog.entry_of(incident["code"])
    offset, text = incident["anchor"]
    dated = incident["last"] is not None
    meta = {"approx_time": not dated, "repeats": incident["count"]}
    if dated and incident["first"] is not None \
            and incident["last"] > incident["first"]:
        meta["span_s"] = int(incident["last"] - incident["first"])
    # Held in file order, which is also oldest first -- the paragraph has to
    # read the way it was printed, or the cause appears under its consequences.
    lines = [line + (f" (x{n})" if n > 1 else "")
             for line, n in incident["lines"].items()]
    return {
        "ts": incident["last"] if dated else file_when,
        "level": entry.severity, "logger": "app.hailort",
        "message": entry.message,
        # An identity from the CONTENT and the POSITION, not a counter.
        # Nothing wrote these as records, so there is no sequence to quote --
        # but leaving them unstamped meant every poll re-read the same tail
        # and offered the same lines as new rows, and the operator watched one
        # failure pile up five deep. `run` says "scan" rather than a process
        # token so nobody reads this as something a process stamped.
        "seq": _native_seq(offset, text), "run": NATIVE_RUN,
        "event_id": entry.event_id, "event_code": entry.code,
        "category": entry.category, "status": "failed",
        "module": "hailort", "function": "", "file": "", "line": 0,
        "user": "", "session": "", "version": VERSION, "request_id": "",
        "action": entry.action, "detail": "\n".join(lines)[:4000],
        # `repeats` counts the LINES this row stands for, which is the same
        # word the app's own throttle uses for a failure it collapsed
        # (systems/*/app/faults.py). Deliberately not put in the message:
        # "repeated 3x" on a single crash described in three lines would say
        # the accelerator dropped three times, which is the false alarm this
        # whole change exists to stop.
        "meta": meta,
        "source": src,
    }


def _uncovered(native, spooled):
    """The scraped lines no real record already reports.

    THE SAME FAILURE ARRIVED TWICE. When HailoRT loses the service, the app
    both spools a record through Python logging AND prints its own line to
    app.log, so the Logs page showed one event as two rows: a real record
    with a seq and a run, and a scraped one with neither. The scan exists for
    the lines that never reach Python; it must not re-report the ones that
    did.

    Matching is on the CATALOG CODE, not the text: the two halves are worded
    differently on purpose (the record carries the catalog's sentence, the
    scraped line carries HailoRT's own), and the code is the only thing they
    genuinely share. The window is wide because the scraped row's timestamp
    is the file's mtime, minutes adrift of the event -- see
    NATIVE_DUP_WINDOW_S.

    Matched against the records actually being returned, filters and all: a
    record the operator's filter excluded is not on the page, so nothing on
    the page is duplicating it.
    """
    if not native or not spooled:
        return native
    covered = {}
    for row in spooled:
        code = row.get("event_code")
        if code:
            covered.setdefault(code, []).append(float(row.get("ts") or 0.0))
    out = []
    for row in native:
        when = float(row.get("ts") or 0.0)
        stamps = covered.get(row.get("event_code")) or ()
        if any(abs(ts - when) <= NATIVE_DUP_WINDOW_S for ts in stamps):
            continue
        out.append(row)
    return out


def app_records(systems_dir, slug, level=None, contains=None, since=None,
                limit=200):
    """Everything this box's app process for `slug` has to say.

    The data dir is asked of the app itself (detector_control.data_dir): a
    second implementation of that rule here got it wrong once already, and a
    system whose data lives on the NVMe would have had its spool looked for
    beside the package, where nothing writes.
    """
    from core.services import detector_control      # noqa: PLC0415 -- heavy
    try:
        data_dir = detector_control.data_dir(systems_dir, slug)
    except Exception as exc:                        # noqa: BLE001
        logger.debug("[LOGS] no data dir for %s: %s", slug, exc)
        return []
    if not data_dir:
        return []
    src = app_source(slug)
    rows = read_spool(os.path.join(data_dir, SPOOL_FILE), src, level,
                      contains, since, limit)
    # When the process writing app.log started, from the app's OWN state file
    # (app_state is the raw read; status() would probe /proc and merge cameras
    # for a page this one is not drawing). A system that has never run has no
    # state, which is 0 here and simply skips the check.
    started_at = float((detector_control.app_state(systems_dir, slug)
                        or {}).get("started_at") or 0)
    app_log = os.path.join(data_dir, APP_LOG_FILE)
    native = native_records(app_log, src, level, contains, since,
                            started_at=started_at,
                            floor=clear_mark(data_dir, app_log))
    # The spool is the better half of any event both halves saw: it has the
    # real time, the module and a stamped identity. The scrape only fills the
    # gaps it leaves.
    return rows + _uncovered(native, rows)


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------

def merge(entries, state, level=None, contains=None, since=None,
          limit=200, category=None, event_id=None, status=None):
    """This process's records, plus its own app's on a master, newest first.

      * on a MASTER: its ring and that system's app process;
      * on the HUB: its ring only. It no longer asks the masters (Tok Aba,
        2026-10-14); a system's failures are on that system's own page.

    Every row carries `source` ("hub", "master:<slug>", "app:<slug>"), and
    only this process's own sources leave here (own_sources).
    """
    own = local_source(state)
    rows = _tag(list(entries or []), own)
    if own != "hub":
        # A master's records written before it named itself (module import,
        # the start-up checks) went out as "hub". They are this master's.
        for row in rows:
            if row.get("source") == "hub":
                row["source"] = own
    foreign = []
    try:
        systems_dir = getattr(state, "systems_dir", None)
        system = getattr(state, "system", None)
        slug = getattr(system, "slug", None)
        if slug and systems_dir:
            foreign = app_records(systems_dir, slug, level, contains, since,
                                  limit)
    except Exception as exc:                        # noqa: BLE001
        # A page that fails to render because ANOTHER process's log could not
        # be read is worse than a page missing that process's rows: this one
        # is the page somebody opens when the board is already misbehaving.
        logger.warning("[LOGS] could not collect other processes' logs: %s", exc)
    if category:
        foreign = [r for r in foreign if r.get("category") == category]
    if event_id:
        foreign = [r for r in foreign if r.get("event_id") == event_id]
    if status:
        foreign = [r for r in foreign if r.get("status") == status]
    rows.extend(foreign)
    allowed = own_sources(state)
    rows = [r for r in rows if r.get("source") in allowed]
    rows.sort(key=lambda r: float(r.get("ts") or 0.0), reverse=True)
    return rows[:int(limit or 200)]


def sources(rows):
    """Every source present in a page of rows, for the page's filter."""
    return sorted({str(r.get("source") or "") for r in rows if r.get("source")})


def level_counts(rows):
    """How many rows of each level are on THIS page.

    The badge strip used to read logs_service.summary()["counts"], which
    counts the records THIS PROCESS logged, cumulatively since it started and
    never decremented when the ring evicts. On the hub that is the one set of
    rows the page is least about: the strip said "0 CRITICAL" over a screenful
    of red rows from the app processes, and the operator's report was "the
    number didnt increase".

    Counted over the merged set instead, so the number and the table are the
    same fact. Every level appears even at zero -- the strip draws five
    buttons whatever happened, and a missing key would silently draw a 0 that
    means "no such level" rather than "none right now".
    """
    counts = {level: 0 for level in LEVELS}
    for row in rows or ():
        level = str(row.get("level") or "").upper()
        if level in counts:
            counts[level] += 1
    return counts


# ---------------------------------------------------------------------------
# Nothing dies quietly
# ---------------------------------------------------------------------------

def install_hooks(label):
    """Record what kills this process, and what kills its threads.

    Neither hub_server, master_server nor the agent had either hook. An
    unhandled exception went to the default excepthook -- a traceback on a
    terminal nobody is watching, under systemd -- and a thread that raised out
    of its target died printing the same way. The feature that thread was
    running (the retention sweep, the update check, the tunnel reader) stopped
    for good, and every page carried on drawing it as running, because nothing
    anywhere observed the death.
    """
    from core.services import logs as logs_service    # noqa: PLC0415 -- cycle

    def _process(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        try:
            logs_service.log_event(
                "SYS_APP_EXITED", level="CRITICAL", logger="cytron.crash",
                detail=f"{exc_type.__name__}: {exc}", exc=exc,
                message=f"{label} stopped on an unhandled "
                        f"{exc_type.__name__}: {exc}", process=label)
        finally:
            # The traceback still reaches the journal. The ring is in memory,
            # and this process is about to stop existing.
            sys.__excepthook__(exc_type, exc, tb)

    def _thread(args):
        if issubclass(args.exc_type, SystemExit):
            return
        name = getattr(args.thread, "name", None) or "?"
        logs_service.log_event(
            "SYS_APP_EXITED", level="ERROR", logger="cytron.crash",
            detail=f"{args.exc_type.__name__}: {args.exc_value}",
            exc=args.exc_value,
            message=f"thread {name} died on an unhandled "
                    f"{args.exc_type.__name__}: {args.exc_value}",
            process=label, thread=name)

    sys.excepthook = _process
    if hasattr(threading, "excepthook"):                # 3.8+
        threading.excepthook = _thread


def guarded(name, fn, args=(), kwargs=None, once=False):
    """Run a thread's work so that its END is a log record, either way.

    threading.excepthook catches a thread that RAISES. It cannot see the two
    other ways a loop stops: an except block that swallows the error, and a
    plain return. Both leave the feature dead with the UI still showing it
    running, which is the failure that costs a site visit.

    `once=True` for work that is SUPPOSED to finish, where returning is not
    news.
    """
    from core.services import logs as logs_service    # noqa: PLC0415 -- cycle
    try:
        fn(*args, **(kwargs or {}))
    except Exception as exc:                            # noqa: BLE001
        logs_service.log_event("SYS_APP_EXITED", level="ERROR",
                               logger="cytron.crash",
                               detail=f"{type(exc).__name__}: {exc}", exc=exc,
                               message=f"{name} stopped: {exc}", thread=name)
    else:
        if not once:
            logs_service.log_event(
                "SYS_APP_EXITED", level="WARNING", logger="cytron.crash",
                message=f"{name} returned -- that feature has stopped until "
                        "the service is restarted", thread=name)


def spawn(name, target, args=(), daemon=True, once=False):
    """threading.Thread, named, with guarded() around the target."""
    return threading.Thread(target=guarded, args=(name, target, tuple(args)),
                            kwargs={"once": once}, name=name, daemon=daemon)
