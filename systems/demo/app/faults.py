# -*- coding: utf-8 -*-
"""
faults.py -- the app process's logging, and the failure spool the dashboard reads.

Cameras, the accelerator, detection and alert sending all run in THIS process,
which is spawned per system and had no logging handler at all: everything it
knew went out as print() into app.log, and no route reads app.log. A board
whose camera had been unplugged and whose accelerator client had been dropped
showed a Logs page holding nothing but the hub's own request lines, while the
answer sat in a file only an SSH session could reach.

TWO OUTPUTS, for two different readers.

  * stdout, as before. The app is spawned with its stdout pointed at app.log
    (app/runtime.py start()), and that file is what people SSH for today;
    changing its shape mid-change would break every habit and every paste in
    the issue tracker. Converted prints come out of the formatter looking like
    the prints they replaced.

  * <data_dir>/faults.jsonl -- one JSON record per WARNING-or-worse line, read
    by the master and merged into the Logs page (core/services/log_collect.py).

WHY A FILE, and not an HTTP push to the master or a ring the master polls.
Three facts about this product decide it:

  * it has to work with PostgreSQL down, which is exactly when an operator is
    on the Logs page. A file involves no database at all;
  * it must not write continuously to an SD card. Only WARNING and worse is
    spooled -- progress chatter goes to stdout and stops there -- and a
    failure that repeats every frame is collapsed by _Throttle, so a camera
    failing at 30 fps costs one line per REPEAT_S, not thirty a second;
  * a process that CRASHES must still leave its last records behind. That is
    the case this module exists for: the app re-execs itself when the
    accelerator service drops it, and an in-memory ring polled over HTTP dies
    with the process that held the evidence. A push loses the same records
    whenever the master is the thing that restarted.

A file also keeps the app free of the master: no port, no auth, and no
ordering between two processes that are started independently.

Everything here is best-effort. A spool that cannot be written (read-only
media, a full disk) must never stop detection -- it degrades to stdout only.
"""

import collections
import itertools
import json
import logging
import os
import re
import sys
import threading
import time
import uuid

#: The structured file the master reads. Beside state.json and events.jsonl in
#: the system's data dir, so it follows the data wherever that was moved to.
SPOOL_FILE = "faults.jsonl"

#: Bound on the spool. One previous file is kept (faults.jsonl.1), the shape
#: app.log and events.jsonl are already bounded with, so a system failing all
#: night cannot fill the disk the footage lives on.
SPOOL_MAX_BYTES = 1024 * 1024

#: A record no worse than this never reaches the spool. Operators chase
#: failures here; "camera opened" is stdout's job.
SPOOL_LEVEL = logging.WARNING

#: How often one repeating failure may be spooled. The repeats are counted and
#: reported on the next line through, so nothing is hidden -- but a camera
#: that fails on every frame writes twice a minute instead of thirty a second.
REPEAT_S = 30.0

#: How often app.log gets a line saying what time it is.
#:
#: WHY THE FILE NEEDS ONE AT ALL. app.log carries no times -- it is this
#: process's raw stdout, and the lines are the prints they always were. The
#: Logs page scrapes HailoRT's native errors out of it (HailoRT writes C++ to
#: fd 2 and will never carry a timestamp of its own) and had nothing to date
#: them by except the FILE's mtime, so six crashes spread over an afternoon
#: all arrived on the page claiming the same second. A line above them saying
#: the time is the only thing that can date them.
#:
#: WHY A MARKER RATHER THAN A STAMP ON EVERY LINE. Stamping each line costs
#: ~20 bytes of a log capped at 16 MB -- about a fifth of the history an
#: operator has to read back through -- changes the shape of every line people
#: grep and paste into tickets, and would still leave the prints that bypass
#: this formatter (app/config.py says its storage lines before logging
#: exists) unstamped, which is worse than unstamped everywhere. One line a
#: minute dates everything below it to the minute, which is the resolution the
#: page needs, and leaves every existing line exactly as it reads today.
#:
#: Written only when something else is written: a silent app writes nothing.
STAMP_EVERY_S = 60.0

#: Records kept in memory here. Parity with the ring the hub and the masters
#: keep (core/services/logs.py): the process that failed can answer "what just
#: happened" without reading its own file back.
RING_SIZE = 400

#: This process run, and its record counter -- the record contract's `run` and
#: `seq` (core/services/logs.py RECORD_FIELDS).
#:
#: THE SPOOL IS WHY BOTH ARE NEEDED. `seq` counts from 1 within one run, and
#: this file OUTLIVES the run that wrote it: the app re-execs itself when the
#: accelerator service drops it, so faults.jsonl routinely holds two runs'
#: records, numbered 1, 2, 3 ... 1, 2, 3. Deduping on `seq` alone would drop
#: the second run's records as copies of the first's -- at exactly the moment
#: somebody is reading the file, because the restart IS the incident.
#:
#: `run` closes it: a fresh token per process start, so source + run + seq is
#: exact. A page seeing a `run` it has not seen before is looking at a process
#: that restarted, which is worth drawing rather than hiding.
RUN_ID = uuid.uuid4().hex[:8]

#: itertools.count().__next__ is atomic under the GIL, so no lock is needed on
#: a path every spooled record goes through -- including records written from
#: the grab thread while the main loop is writing its own.
_seq = itertools.count(1)

# -- the event catalog, as far as this process can see it -------------------
#
# CODES, NOT WORDING. Every name here is a code from
# core/services/log_catalog.py, which owns the operator-facing sentence and
# the "what to do next" line. This process cannot import that module -- the
# app package is standalone by design, which is what lets the master load
# app/config.py by file path on a bare Pi -- so the codes are repeated as
# bare strings and nothing else is.
#
# The app therefore writes FACTS (the driver's complaint, the pid holding the
# device) and a code; the master turns the code into prose when it reads the
# spool (core/services/log_collect.py). That is the catalog's own rule -- the
# call site passes facts, never prose -- and it is what stops the same failure
# reading two ways depending on which process noticed it.
#
# A code that is not in the catalog degrades to an uncatalogued record rather
# than failing: a typo here must cost a category on one row, not a camera.
CAM_NOT_FOUND = "CAM_NOT_FOUND"            # the configured device is not there
CAM_BUSY = "CAM_BUSY"                      # another process holds the device
CAM_DISCONNECTED = "CAM_DISCONNECTED"      # opened once, now gone for good
CAM_STREAM_STALLED = "CAM_STREAM_STALLED"  # still open, no usable frames
CAM_UNREACHABLE = "CAM_UNREACHABLE"        # a network camera did not answer
NET_SMTP_FAILED = "NET_SMTP_FAILED"        # an email alert did not send
NET_TELEGRAM_FAILED = "NET_TELEGRAM_FAILED"    # a Telegram alert did not send
NET_UPSTREAM_TIMEOUT = "NET_UPSTREAM_TIMEOUT"  # a webhook/publish did not land
NET_MODBUS_NO_REPLY = "NET_MODBUS_NO_REPLY"    # the PLC did not answer
NET_MQTT_DISCONNECTED = "NET_MQTT_DISCONNECTED"
MDL_LOAD_FAILED = "MDL_LOAD_FAILED"        # the HEF or its labels would not load
MDL_NOT_FOUND = "MDL_NOT_FOUND"            # the selected model is not installed
AI_HAILO_ABSENT = "AI_HAILO_ABSENT"        # no accelerator, or no runtime
AI_HAILO_BUSY = "AI_HAILO_BUSY"            # held by another system on this box
AI_SERVICE_UNAVAILABLE = "AI_SERVICE_UNAVAILABLE"  # detect() raised
AI_SERVICE_CRASHED = "AI_SERVICE_CRASHED"  # the accelerator service dropped us
AI_PIPELINE_STALLED = "AI_PIPELINE_STALLED"    # running, producing nothing
SYS_APP_EXITED = "SYS_APP_EXITED"          # this whole process died
SYS_TASK_STOPPED = "SYS_TASK_STOPPED"      # one background worker died
SYS_CONFIG_INVALID = "SYS_CONFIG_INVALID"  # a config file would not load
SYS_OUTPUT_FAILED = "SYS_OUTPUT_FAILED"    # an alert or output action failed
STO_WRITE_FAILED = "STO_WRITE_FAILED"      # a file could not be written

#: The `extra=` key an enriched record travels under, the same one the ring in
#: core/services/logs.py reads. One opaque dict rather than loose keys because
#: logging refuses to let `extra` overwrite a LogRecord attribute, and
#: `module`, `filename` and `lineno` are all taken.
EVENT_KEY = "cytron_event"

_ring = collections.deque(maxlen=RING_SIZE)
_lock = threading.Lock()
_installed = False


# -- redaction ---------------------------------------------------------------
#
# The same rules as core/services/logs.py redact(), repeated here for the same
# reason the catalog codes above are repeated: this package is standalone by
# design (it is what lets the master load app/config.py by file path on a bare
# Pi) and cannot import core.
#
# It matters more here than in a log file. An output's failure text is now
# PUBLISHED -- it becomes out_<id>_error and travels to every dashboard on the
# live poll -- and the two credentials this process actually handles, an SMTP
# or webhook URL carrying a password and a Telegram bot token, are both quoted
# verbatim by the exceptions urllib and smtplib raise.

#: Match key/value credentials in normal log and JSON-like text.
_SECRET_RE = re.compile(
    r"""(["']?)                                    # quote around the key, if any
        ([\w.\-]*(?:token|password|passwd|secret|authorization|api[_-]?key)[\w.\-]*)
        \1                                         # ...closed the same way
        \s*[=:]\s*
        (?:bearer\s+|basic\s+|token\s+)?           # scheme word, not the secret
        (?:                                        # the credential itself:
            (["'])[^"']*\3                         #   quoted, spaces and all
          | [^\s"',;)}\]]+                         #   or bare, up to a separator
        )
    """, re.IGNORECASE | re.VERBOSE)

#: Mask passwords in URLs while retaining the user and host.
# The password runs to the LAST "@" before the host: FFmpeg and urllib both
# split there, so "admin:P@ss@cam" is a working camera address, and stopping
# at the first "@" (or at a ":" inside the password) left it on screen.
# Parsed, not matched by one pattern (Pipi L5, 2026-10-11): the old class
# stopped the password at "/", so "admin:ab/cd@cam" kept it whole. The URL
# runs to the first character no URL carries unquoted; its userinfo is
# everything before that URL's last "@". "host:554/a@b" is a host, a port and
# a path, not a user and a password (pinned as such by smoke_camurlmask).
# core/services/logs.py and frontend/src/lib/camErrors.js keep the same rule.
_URL_RE = re.compile(r"""([A-Za-z][A-Za-z0-9+.\-]*://)([^\s'"<>]*)""")
_PORT_PATH_RE = re.compile(r"\d{1,5}/")
#: A label is free text, so the userinfo may come with no scheme at all: a
#: camera saved as "IP camera admin:pw@10.0.0.5" (a pasted URL turned name).
_BARE_RE = re.compile(r"""(?<![\w/:])([^\s'"<>/:@]+):(?!//)([^\s'"<>]*)@(?=[^\s'"<>@]+)""")


def _userinfo_split(rest):
    """(user, password, after) for the part of a URL after "scheme://", or
    None when it carries no password (no "@", no ":", a host:port/path)."""
    at = rest.rfind("@")
    if at <= 0:
        return None
    user, colon, password = rest[:at].partition(":")
    if not colon or not password or "/" in user or _PORT_PATH_RE.match(password):
        return None
    return user, password, rest[at:]


def mask_url_passwords(text, hidden="***"):
    """`text` with the password of every URL in it replaced by `hidden`."""
    def one(m):
        parts = _userinfo_split(m.group(2))
        return m.group(0) if parts is None else f"{m.group(1)}{parts[0]}:{hidden}{parts[2]}"
    return _URL_RE.sub(one, str(text))


def mask_label(text):
    """A camera NAME, safe to publish: URL passwords masked, and a bare
    user:password@host (no scheme) masked the same way. For the label
    state.json, events and the HUD carry, never for a whole log line."""
    masked = mask_url_passwords(text)
    return _BARE_RE.sub(lambda m: f"{m.group(1)}:***@", masked)

#: A Telegram bot token as it appears in the API PATH. Neither rule above can
#: see it: it is not an assignment, and it is not a user:password@host either
#: -- the token IS the path (/bot<digits>:<secret>/sendMessage). urllib quotes
#: the whole URL in several of its errors, so without this the one credential
#: that grants write access to somebody's chat would be published on a card.
_BOT_PATH_RE = re.compile(r"/bot\d+:[\w\-]+")


def mask_urls(text):
    """Only the URL-password rule of redact(): what every log line gets
    (the key/value rule would rewrite ordinary log prose)."""
    return mask_url_passwords(text)


def redact(text):
    """Mask secret-looking assignments, URL credentials and bot tokens."""
    masked = mask_url_passwords(text)
    masked = _BOT_PATH_RE.sub("/bot***", masked)
    return _SECRET_RE.sub(lambda m: f"{m.group(2)}=***", masked)


def event(code, **meta):
    """`extra=` for a log call, so the record carries its catalog code.

    A sentence an operator can read is not a thing a page can group, count or
    filter; the code is what "every camera failure on this board" keys on, and
    what a support call can be quoted down a phone line.
    """
    return {EVENT_KEY: {"event_code": code, "meta": meta or {}}}


def _code_of(record):
    """The catalog code on a record, or "" for a plain logging call."""
    return ((getattr(record, EVENT_KEY, None) or {}).get("event_code") or "")


def logger(name):
    """A logger for an app module. Named app.<module>, so the page's filter can
    pick the app out of a list holding three processes' loggers."""
    short = (name or "app").split(".")[-1]
    return logging.getLogger("app" if short == "app" else "app." + short)


def spool_path(data_dir):
    return os.path.join(str(data_dir), SPOOL_FILE)


class _Format(logging.Formatter):
    """app.log, reading the way it read before.

    Prints in this app carry their own bracket tag ("[APP] camera ...",
    "[CAM] ...", "[FN] ..."), and those tags are what people grep for on a
    board. A message that already starts with one is left alone; everything
    else gets [APP], and WARNING or worse says so -- scrolling app.log, a
    failure and a progress line used to look identical.
    """

    def format(self, record):
        text = record.getMessage()
        if not text.startswith("["):
            tag = "[APP] " if record.levelno < logging.WARNING \
                else "[APP] %s: " % record.levelname
            text = tag + text
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        # Camera passwords never reach app.log: a dozen call sites log a
        # camera's source as is (camera.py "camera %r: ..."), and an RTSP
        # source carries user:password. Masked here, once, for all of them.
        return _time_marker(record.created) + mask_urls(text)


#: When the last time marker was written. Module level rather than an
#: instance attribute because the handler is built once but the file is what
#: is being marked, and a second handler on the same file must not double the
#: markers.
_marked_at = 0.0


def _time_marker(created):
    """"[APP] ==== <time> ====\\n", or "" if the log was marked recently.

    The same shape as the run marker app/runtime.py writes at start, so one
    regex in core/services/log_collect.py reads both, and so a person
    scrolling app.log sees one kind of ruler rather than two.
    """
    global _marked_at
    if created - _marked_at < STAMP_EVERY_S:
        return ""
    _marked_at = created
    return "[APP] ==== %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S",
                                                  time.localtime(created))


class _Throttle:
    """How many times this failure repeated since it was last written.

    Keyed on the message TEMPLATE, not the formatted line: "camera %r gave no
    frames after %s" with a changing number in it is one failure repeating,
    and keying on the final text would defeat the guard and write every frame
    of it to the card.
    """

    def __init__(self):
        self._seen = {}

    def check(self, record):
        key = (record.name, _code_of(record), str(record.msg))
        now = time.time()
        with _lock:
            when, count = self._seen.get(key, (0.0, 0))
            if now - when < REPEAT_S:
                self._seen[key] = (when, count + 1)
                return None
            self._seen[key] = (now, 0)
            if len(self._seen) > 500:           # bounded on a long run
                self._seen = dict(list(self._seen.items())[-250:])
        return count


class SpoolHandler(logging.Handler):
    """Append failures to the file the master reads.

    Opened and closed per record rather than held open: the app is SIGKILLed
    when a stop times out, and whatever a buffered writer was holding at that
    moment is precisely the evidence of why it had to be killed.
    """

    def __init__(self, data_dir):
        logging.Handler.__init__(self, SPOOL_LEVEL)
        self.path = spool_path(data_dir)
        self._throttle = _Throttle()

    def emit(self, record):
        repeats = self._throttle.check(record)
        if repeats is None:
            return
        try:
            entry = to_entry(record, repeats)
            _ring.append(entry)
            self._rotate()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:                                   # noqa: BLE001
            # A spool that cannot be written is a page missing a line; a spool
            # that RAISES here takes down the logging call inside the camera
            # recovery ladder, and the recovery with it.
            pass

    def _rotate(self):
        try:
            if os.path.getsize(self.path) < SPOOL_MAX_BYTES:
                return
        except OSError:
            return
        try:
            os.replace(self.path, self.path + ".1")
        except OSError:
            pass


def to_entry(record, repeats=0):
    """One log record as the dict the Logs page renders.

    Deliberately the keys core/services/logs.py already puts in its ring --
    ts, seq, run, level, logger, message, module, line, and traceback when
    there is one -- so a record that crossed a process boundary needs no
    special case in the page. The additions are `event_code`, `meta` and the
    repeat count.

    `source` is NOT written here. On the record contract that key names the
    PROCESS that noticed a failure ("app:line1"), and only the master reading
    this file knows which system it belongs to; the module and line it used to
    hold are their own fields now.

    `message` here is the app's own technical text. Where a code is attached
    the master moves that text into `detail` and puts the catalog's wording in
    `message`, which is the rule that keeps an operator-facing sentence out of
    a call site's f-string.
    """
    try:
        # The spool reaches the Logs page: same password mask as app.log.
        message = mask_urls(record.getMessage())
    except Exception:                                       # noqa: BLE001
        message = "<unformattable log record>"
    if repeats:
        message += " (repeated %dx in the last %.0fs)" % (repeats, REPEAT_S)
    entry = {"ts": record.created, "seq": next(_seq), "run": RUN_ID,
             "level": record.levelname, "logger": record.name,
             "message": message[:2000], "module": record.module,
             "line": int(record.lineno or 0)}
    code = _code_of(record)
    if code:
        entry["event_code"] = code
        meta = (getattr(record, EVENT_KEY, None) or {}).get("meta")
        if meta:
            entry["meta"] = meta
    if record.exc_info:
        entry["traceback"] = mask_urls("".join(
            logging.Formatter().formatException(record.exc_info)))[:8000]
    return entry


def detector_event(exc, default=MDL_LOAD_FAILED):
    """Which catalog code a failed detector build or inference belongs under.

    The failure raises from four layers -- the HailoRT bindings, the VDevice,
    the HEF loader, the labels loader -- and the caller that catches it is
    generic (it builds any detector kind). Classified here, in one place:
    "the accelerator is missing", "another system has it" and "the model file
    is missing" send an operator to three different jobs, and all three used
    to arrive as one line saying the detector failed to load.

    The runtime's own numbers are matched as well as its words, because what
    reaches Python is often only "CHECK_SUCCESS failed with status=62".
    """
    text = str(exc)
    low = text.lower()
    if ("already in use" in low or "OUT_OF_PHYSICAL_DEVICES" in text
            or "(74)" in text or "status=74" in text):
        return AI_HAILO_BUSY
    # 62 is HAILO_COMMUNICATION_CLOSED, 77 is HAILO_RPC_FAILED: both mean the
    # accelerator SERVICE dropped this client, usually because a sibling
    # system on the same chip stopped or reloaded.
    if ("COMMUNICATION_CLOSED" in text or "RPC_FAILED" in text
            or "status=62" in text or "status=77" in text
            or "(62)" in text or "(77)" in text):
        return AI_SERVICE_CRASHED
    if ("hailo_platform" in low or "hailort runtime" in low
            or "no hailo accelerator" in low):
        return AI_HAILO_ABSENT
    if "hef" in low or "labels" in low or "model" in low:
        return MDL_NOT_FOUND if "no hef" in low or "not at" in low             else MDL_LOAD_FAILED
    # `default` is the caller's job, not a guess: the same unrecognised
    # exception means "this model will not load" when a detector is being
    # BUILT and "detection has stopped" when one is running.
    return default


def recent(limit=200):
    """The newest records this process holds, newest first."""
    return list(_ring)[-int(limit or 200):][::-1]


def install(data_dir, level=logging.INFO):
    """Point this process's logging at app.log and the spool. Idempotent.

    Called from app/run.py main() before anything can fail. Every module here
    logs through logging.getLogger, including the ones that always did
    (hailo.py, buses.py, publisher.py) and whose warnings have been going
    nowhere since they were written -- installing a handler is what turns
    those on.
    """
    global _installed
    root = logging.getLogger()
    if _installed:
        root.setLevel(level)
        return root
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_Format())
    root.addHandler(stream)
    try:
        root.addHandler(SpoolHandler(data_dir))
    except Exception:                                       # noqa: BLE001
        pass
    root.setLevel(level)
    # The chatty libraries this app loads. At INFO, pymodbus alone narrates
    # every transaction into app.log, which buries the camera line somebody
    # is scrolling for.
    for name in ("pymodbus", "paho", "urllib3", "asyncio", "PIL",
                 "matplotlib", "hailo_platform", "ultralytics"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _installed = True
    return root


# -- nothing dies quietly ----------------------------------------------------

def install_hooks(label="app"):
    """Record what kills this process, and what kills its threads.

    Without this an unhandled exception reaches the default excepthook, which
    prints a traceback to stderr -- into app.log, where nothing reads it --
    and a thread that raises out of its target dies the same way. The feature
    that thread was running stops, and every page keeps drawing it as running,
    because nothing observed the death.
    """
    log = logging.getLogger("app.faults")

    def _process(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("%s stopped on an unhandled %s: %s", label,
                     exc_type.__name__, exc, exc_info=(exc_type, exc, tb),
                     extra=event(SYS_APP_EXITED, process=label))

    def _thread(args):
        # A worker dying and the whole app dying are not the same event and
        # must not share a code: one means a feature stopped and detection
        # carried on, the other means the board stopped detecting. An
        # operator filtering on either wants exactly one of them.
        if issubclass(args.exc_type, SystemExit):
            return
        name = getattr(args.thread, "name", None) or "?"
        log.error("thread %s died on an unhandled %s: %s", name,
                  args.exc_type.__name__, args.exc_value,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                  extra=event(SYS_TASK_STOPPED, thread=name))

    sys.excepthook = _process
    if hasattr(threading, "excepthook"):                    # 3.8+
        threading.excepthook = _thread


def guarded(name, fn, args=(), kwargs=None, once=False):
    """Run a thread's work so that its END is a log record, either way.

    threading.excepthook catches a thread that RAISES. It cannot see the other
    two ways a loop stops: an except block that swallows the error (several
    loops here swallow by design, so one bad read cannot kill the poller), and
    a plain return. Both leave the feature dead with the UI still showing it
    running, which is the failure that costs a site visit.

    `once=True` for work that is SUPPOSED to finish (the event archiver, one
    send): returning is not news there, and reporting it would bury the real
    deaths under a line per completed job.
    """
    log = logging.getLogger("app.faults")
    try:
        fn(*args, **(kwargs or {}))
    except Exception as exc:                                # noqa: BLE001
        log.error("%s stopped: %s", name, exc, exc_info=True,
                  extra=event(SYS_TASK_STOPPED, thread=name))
    else:
        if not once:
            log.warning("%s returned -- that feature has stopped until the "
                        "app is restarted", name,
                        extra=event(SYS_TASK_STOPPED, thread=name))


def spawn(name, target, args=(), daemon=True, once=False):
    """threading.Thread, named, with guarded() around the target."""
    return threading.Thread(target=guarded, args=(name, target, tuple(args)),
                            kwargs={"once": once}, name=name, daemon=daemon)
