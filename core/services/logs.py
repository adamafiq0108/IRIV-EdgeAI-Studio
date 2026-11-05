# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
logs.py -- an in-memory log ring the dashboard can show.

Operators debug through a browser, not a terminal. Without this, "the email
digest failed last night" means an SSH session and `journalctl`, and on a
customer site nobody has that access. With it, the last N log records -- and
every unhandled request error -- are a page.

Deliberately a **bounded ring in memory**, not a table:

  * it must work with the database down, which is exactly when logs matter;
  * a log table is a disk-filling foot-gun on a Pi-class host;
  * anything worth keeping forever is an audit action (core/services/audit.py),
    not a log line.

Secrets never reach here: the formatter drops any record whose message matches
a secret-ish key, because a stack trace with a bearer token in it becomes a
credential leak the moment the page is shared.

WHAT EACH RECORD CARRIES, AND WHY IT GREW. The ring used to hold five fields
(ts, level, logger, message, source) and that was not enough to answer the two
questions support actually asks. "Which of the four systems on that board?"
needed the event's own identity, not a sentence. "Who was logged in and what
were they doing?" needed the user, the session and the request id, all of which
the process already had and threw on the floor -- `record.funcName` and
`record.pathname` arrive on EVERY LogRecord for free and were simply not being
read, and `request.state.session_sid` has existed since sessions shipped and
has never once been logged. The version comes from VERSION because a bug report
from a board nobody knows the version of is a bug report nobody can reproduce.

ONE FAILURE, ONE RECORD. A single failing request used to write two lines: an
access line from cytron.access and an error line from cytron.error, neither of
which held the whole story -- the access line knew the route and the status,
the error line knew the exception, and the operator got to see the same event
twice with the cause in neither. The two are now joined by request id BEFORE
anything is written, through a small pending table below, so what reaches the
ring is one row carrying both halves. See `note_failure`/`log_request` for the
ordering, which is the fiddly part: the access log is the OUTERMOST middleware
and the error handlers run inside it, except the unhandled-exception handler,
which Starlette installs outside it.

The user-facing `message` and the technical `detail` are separate fields on
purpose. `message` is catalog wording fit for a panel on a factory wall;
`detail` is the errno, the path, the driver's complaint -- the only text that
ever actually diagnoses anything. Mixing them is how "could not save:
[Errno 13] Permission denied /opt/cytron/store/..." became an operator-facing
string.
"""

import collections
import itertools
import logging
import os
import re
import sys
import time
import traceback
import uuid

from core.services import log_catalog as catalog
from core.services.redact import mask_url_passwords
from core.version import VERSION

MAX_RECORDS = 2000

# Keep extra room during a short debugging session.
DEBUG_RECORDS = 8000

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: The two values `status` may take. The operator asked for SUCCESS and FATAL
#: as severities; they were declined partly because THIS field already answers
#: "did it work" (see core/services/log_catalog.py for the full reasoning).
STATUSES = ("success", "failed")

#: What a record's `source` may say: which PROCESS on the board noticed it.
#: A board runs a hub, a master per system and a detection app per running
#: system, and the Logs page now shows all three together. The exact strings
#: are the ones core/services/log_collect.py already produces -- one
#: vocabulary, not two, or the page's source filter silently splits in half.
#:
#: `source` used to hold "module:lineno". That was a duplicate of `module` and
#: `line`, which are their own fields now, and it forced the page to tell the
#: two meanings apart with a regex on the shape -- a guess no record should
#: make anyone take.
SOURCE_HUB = "hub"


def source_of_master(slug):
    return f"master:{slug}"


def source_of_app(slug):
    return f"app:{slug}"


#: This process run, and its record counter.
#:
#: WHY BOTH. The page keys rows so it can dedupe across refreshes and count
#: what arrived while it was paused, and it was doing that on `ts|message`.
#: Two records in the same millisecond with the same text are not
#: hypothetical -- a retry loop produces exactly that -- so the key was very
#: nearly exact, which is the worst kind.
#:
#: `seq` is monotonic WITHIN ONE PROCESS RUN, starting at 1. It is not a
#: global clock and cannot be: three processes on a board each count their
#: own, and none can see the others'. Across a restart it RESETS to 1, so
#: `seq` alone collides with the previous run -- which matters, because the
#: detection app re-execs itself when the accelerator service drops it, and
#: its spool survives that restart with both runs' records in it.
#:
#: `run` closes that hole: a fresh token per process start, so
#: `source + run + seq` is exact everywhere, including rows collected from
#: another process. A page seeing a `run` it has not seen before is looking
#: at a process that restarted, which is worth drawing rather than hiding.
RUN_ID = uuid.uuid4().hex[:8]

#: itertools.count().__next__ is atomic under the GIL, so no lock is needed on
#: a path every log record in the process goes through.
_seq = itertools.count(1)

#: What THIS process calls itself. "hub" until told otherwise: the hub is the
#: only process that cannot name itself from its own state, and a master or an
#: app sets this at startup through set_origin() / install(origin=...).
_origin = SOURCE_HUB

#: Every key on a ring record, in the order the Logs page's columns run. The
#: contract the frontend is built against; served by /api/logs/catalog so
#: there is one list rather than one per consumer. `traceback` is deliberately
#: absent: it is present only when there was one.
RECORD_FIELDS = (
    "ts", "seq", "run", "level", "logger", "source", "message", "detail",
    "event_id", "event_code", "category", "status", "action", "module",
    "function", "file", "line", "user", "session", "version", "request_id",
    "http_status", "meta",
)

#: The single `extra=` key every enriched record travels under. One opaque
#: dict rather than a dozen loose keys because logging refuses to let `extra`
#: overwrite a LogRecord attribute, and `module`, `filename` and `lineno` --
#: exactly the fields we want to override when the event happened somewhere
#: other than the logging call -- are all taken.
EVENT_KEY = "cytron_event"

# Only application DEBUG messages belong in the operator log.
OUR_LOGGERS = ("cytron", "core", "master_server", "hub_server", "agent",
               "systems", "app", "__main__")

# Avoid library debug output filling the ring.
NOISY_LOGGERS = ("matplotlib", "fontTools", "PIL", "pymodbus", "paho",
                 "asyncio", "urllib3", "uvicorn", "websockets", "multipart",
                 "python_multipart", "weasyprint", "psycopg2", "hailo_platform",
                 "ultralytics", "charset_normalizer", "watchfiles")

# Match key/value credentials in normal log and JSON-like text.
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

# Mask passwords in URLs while retaining the user and host.
# The password runs to the LAST "@" before the host: FFmpeg and urllib both
# split there, so "admin:P@ss@cam" is a working camera address, and stopping
# at the first "@" (or at a ":" inside the password) left it on screen. A
# "/" inside it too (Pipi L5): the rule is parsed, in core/services/redact.py
# mask_url_passwords, the same one the app keeps in app/faults.py.

#: Where this checkout lives, so a file path on a record reads
#: "core/routes/models.py" rather than the board's install prefix. A customer
#: screenshot full of /opt/cytron/releases/1.0.6/... is noise, and the prefix
#: differs per box (.234 is /opt/cytron, .63 is /home/pi) which makes two
#: reports of the same fault look like two faults.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_records = collections.deque(maxlen=MAX_RECORDS)
_counts = {level: 0 for level in LEVELS}

#: Half-finished failures, keyed by request id, waiting for their other half.
#: Bounded and popped on flush: a slot whose partner never arrives (a request
#: killed mid-flight) must not pin memory on a box that runs for months.
_pending = collections.OrderedDict()
PENDING_MAX = 64

# Capture level is intentionally reset when the process restarts.
_capture = logging.INFO
_root_level = logging.WARNING


def redact(text):
    """Mask secret-looking assignments and URL credentials anywhere in a line."""
    masked = mask_url_passwords(text)
    return _SECRET_RE.sub(lambda m: f"{m.group(2)}=***", masked)


def _is_ours(name):
    return (name or "").split(".", 1)[0] in OUR_LOGGERS


def origin():
    """What this process stamps on the records it writes."""
    return _origin


def set_origin(name):
    """Name this process on its own records.

    Called once at startup by whoever knows the slug -- the hub cannot work
    it out and does not need to. Left as a separate call rather than guessed
    from the environment because a master that mislabels its rows as the
    hub's sends support to the wrong process, which is worse than a board
    that says "hub" for everything.
    """
    global _origin
    _origin = str(name or SOURCE_HUB)
    return _origin


def _short_path(path):
    """A file path trimmed to its place in this repo."""
    text = str(path or "")
    if not text:
        return ""
    try:
        absolute = os.path.abspath(text)
    except (OSError, ValueError):
        return text
    if absolute.startswith(_REPO + os.sep):
        absolute = absolute[len(_REPO) + 1:]
    return absolute.replace(os.sep, "/")


def _status_of(levelno, given):
    """Explicit status wins; otherwise the level decides.

    Defaulting rather than leaving it blank matters because the Logs page
    filters on this column: a row with no status is a row that vanishes from
    both "failed" and "success", which is worse than a reasonable guess. An
    ERROR is by definition something that did not succeed; a WARNING may well
    have (a retry that worked), so only ERROR and above default to failed.
    """
    if given in STATUSES:
        return given
    return "failed" if levelno >= logging.ERROR else "success"


def _location(exc=None, depth=2):
    """Where the event happened: (function, file, line).

    An exception's DEEPEST frame, not the handler's, when one is available --
    the middleware that catches a failure is never the code that caused it,
    and a record pointing at the catch site sends the next person to the wrong
    file. Falls back to the caller's frame for ordinary events.
    """
    if exc is not None and getattr(exc, "__traceback__", None) is not None:
        try:
            frames = traceback.extract_tb(exc.__traceback__)
        except Exception:                          # noqa: BLE001
            frames = []
        if frames:
            last = frames[-1]
            return (last.name or "", _short_path(last.filename),
                    int(last.lineno or 0))
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "", "", 0
    return (frame.f_code.co_name, _short_path(frame.f_code.co_filename),
            int(frame.f_lineno or 0))


def _clean_meta(meta):
    """Metadata, redacted and bounded.

    Numbers and booleans pass through so the page can sort on them; everything
    else is stringified and run through the redactor, because the natural
    thing to hand a log call is the config dict that just failed to load, and
    that dict routinely holds an app password.
    """
    out = {}
    for key, value in (meta or {}).items():
        if isinstance(value, bool) or isinstance(value, (int, float)) \
                or value is None:
            out[str(key)] = value
        else:
            out[str(key)] = redact(value)[:500]
    return out


class RingHandler(logging.Handler):
    """Attach once at startup; every logger in the process feeds this page."""

    def emit(self, record):
        if record.levelno < logging.INFO and not _is_ours(record.name):
            return
        try:
            message = redact(record.getMessage())
        except Exception:                          # noqa: BLE001
            message = "<unformattable log record>"
        event = getattr(record, EVENT_KEY, None) or {}
        # Everything below falls back to what the LogRecord already knows, so
        # a plain logger.info() somewhere in the product still lands as a
        # complete row rather than one with eight empty columns.
        entry = {
            "ts": record.created,
            "seq": next(_seq),
            "run": RUN_ID,
            "level": record.levelname,
            "logger": record.name,
            "message": message[:2000],
            # The ORIGIN, not "module:lineno" -- that spelling duplicated the
            # module and line fields below in a worse format.
            "source": event.get("source") or _origin,
            "event_id": event.get("event_id") or "",
            "event_code": event.get("event_code") or "",
            "category": event.get("category") or "",
            "status": _status_of(record.levelno, event.get("status")),
            "module": event.get("module") or record.module,
            "function": event.get("function") or record.funcName or "",
            "file": event.get("file") or _short_path(record.pathname),
            "line": int(event.get("line") or record.lineno or 0),
            "user": event.get("user") or "",
            "session": event.get("session") or "",
            "version": VERSION,
            "request_id": event.get("request_id") or "",
            "action": event.get("action") or "",
            "detail": event.get("detail") or "",
            # A real field rather than a key in `meta`, because it sits beside
            # request_id/user/session, which are request facts at this level
            # too, and the page sorts and filters on it. It was `meta.
            # status_code` as well for one afternoon; shipping both spellings
            # is how a details panel ends up reading the one nobody fills in.
            "http_status": int(event.get("http_status") or 0),
            "meta": event.get("meta") or {},
        }
        if record.exc_info:
            entry["traceback"] = redact(
                "".join(traceback.format_exception(*record.exc_info)))[:8000]
        _records.append(entry)
        _counts[record.levelname] = _counts.get(record.levelname, 0) + 1


def install(level=logging.INFO, origin=None):
    """Install one ring handler and apply the requested capture level.

    `origin` names this process on every record it writes; see set_origin().
    """
    global _root_level
    if origin:
        set_origin(origin)
    root = logging.getLogger()
    existing = next((h for h in root.handlers if isinstance(h, RingHandler)), None)
    if existing is None:
        _root_level = root.level
        root.addHandler(RingHandler())
        _quiet_noisy_libraries()
    set_capture(level)
    return next(h for h in root.handlers if isinstance(h, RingHandler))


def _quiet_noisy_libraries():
    """Pin the chatty third-party loggers so they never emit DEBUG at all."""
    for name in NOISY_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)


def capture_level():
    """The level the ring is capturing at, as a name."""
    return logging.getLevelName(_capture)


def set_capture(level):
    """Set the runtime capture level and resize the ring if needed."""
    global _capture
    wanted = _level_of(level)
    _capture = wanted
    root = logging.getLogger()
    root.setLevel(min(_root_level or logging.INFO, wanted))
    for handler in root.handlers:
        if isinstance(handler, RingHandler):
            handler.setLevel(wanted)
    _quiet_noisy_libraries()
    _resize(DEBUG_RECORDS if wanted <= logging.DEBUG else MAX_RECORDS)
    logging.getLogger("cytron.logs").info("log capture level is now %s",
                                          capture_level())
    return capture_level()


def _level_of(level):
    """A level name or number -> a number. Anything unknown means INFO."""
    if isinstance(level, int):
        return level if level in (getattr(logging, n) for n in LEVELS) else logging.INFO
    return getattr(logging, str(level).upper(), logging.INFO) \
        if str(level).upper() in LEVELS else logging.INFO


def _resize(capacity):
    """Resize the ring, retaining the newest entries."""
    global _records
    if _records.maxlen == capacity:
        return
    _records = collections.deque(_records, maxlen=capacity)


def capacity():
    return _records.maxlen or MAX_RECORDS


def event_payload(code, status=None, user=None, session=None, request_id=None,
                  detail=None, exc=None, meta=None, location=None,
                  http_status=None, source=None):
    """Build the `extra=` dict for one catalogued event.

    Exposed rather than private because the masters and the app processes log
    through the same catalog and must produce rows in the same shape -- a
    second, hand-rolled payload somewhere else is how the Logs page ends up
    with rows it can only half render.
    """
    entry = catalog.entry_of(code)
    function, path, line = location or _location(exc, depth=3)
    meta = dict(meta or {})
    # The exception TYPE, filled in here rather than left to each call site to
    # remember. It is the one fact worth grouping failures by, and digging it
    # back out of `detail` means parsing prose.
    if exc is not None and "exception" not in meta:
        meta["exception"] = type(exc).__name__
    return {
        "event_id": entry.event_id,
        "event_code": entry.code,
        "category": entry.category,
        "action": entry.action,
        "status": status,
        "module": (path.rsplit("/", 1)[-1] or "").removesuffix(".py"),
        "function": function,
        "file": path,
        "line": line,
        "user": str(user or ""),
        "session": str(session or ""),
        "request_id": str(request_id or ""),
        "http_status": int(http_status or 0),
        "source": str(source or ""),
        "detail": redact(detail)[:4000] if detail else "",
        "meta": _clean_meta(meta),
    }


def log_event(code, level=None, logger=None, detail=None, exc=None,
              status=None, user=None, session=None, request_id=None,
              message=None, **meta):
    """Write one catalogued event to the ring.

    `message` is almost never passed: the catalog entry owns the wording, and
    a call site that supplies its own is exactly how the same failure came to
    read four different ways. It exists for the handful of events whose text
    genuinely varies (the name of the system holding the accelerator).
    """
    entry = catalog.entry_of(code)
    payload = event_payload(code, status=status, user=user, session=session,
                            request_id=request_id, detail=detail, exc=exc,
                            meta=meta, location=_location(exc, depth=2))
    target = logging.getLogger(logger or "cytron.event")
    target.log(_level_of(level or entry.severity), "%s", message or entry.message,
               extra={EVENT_KEY: payload},
               exc_info=exc if exc is not None else None)
    return entry


def note_failure(request_id, **facts):
    """Set aside half a failure until its other half turns up.

    Nothing is written here. Whoever runs LAST for this request calls
    `log_request`, which picks these facts up and emits the single row. The
    ordering is not symmetrical and is worth stating plainly:

      * an HTTPException or a ClientDisconnect is handled INSIDE the access
        middleware, so the handler notes and the access log emits;
      * an unhandled exception is handled OUTSIDE it (Starlette hangs the
        500 handler off ServerErrorMiddleware, which wraps everything), so
        the access middleware notes on its way past and the 500 handler
        emits.

    Get that backwards and the row is written before the interesting half
    exists, which is the two-lines-neither-complete bug in a new shape.
    """
    rid = str(request_id or "")
    if not rid:
        return
    slot = _pending.get(rid) or {}
    slot.update({k: v for k, v in facts.items() if v is not None})
    _pending[rid] = slot
    _pending.move_to_end(rid)
    while len(_pending) > PENDING_MAX:
        _pending.popitem(last=False)


def take_failure(request_id):
    """Pop the noted facts for a request, if any."""
    return _pending.pop(str(request_id or ""), None) or {}


def log_request(method, path, status_code, took_ms=0, request_id=None,
                user=None, session=None, code=None, detail=None, exc=None,
                level=None, **meta):
    """The ONE record a request produces, successful or not.

    Merges anything `note_failure` set aside for the same request id, so the
    route, the status, the user and the exception all arrive on a single row.
    """
    noted = take_failure(request_id)
    took_ms = took_ms or noted.get("took_ms") or 0
    exc = exc if exc is not None else noted.get("exc")
    code = code or noted.get("code") or code_for_status(status_code)
    detail = detail or noted.get("detail") or ""
    entry = catalog.entry_of(code)
    merged = dict(noted.get("meta") or {})
    merged.update(meta)
    merged.update({"method": method, "path": path,
                   "duration_ms": round(float(took_ms), 1)})

    # A raise site's own level (catalog.http_error(level=...)) wins over the
    # catalog severity: it knows the failure was handled.
    level = level or noted.get("level") or \
        (entry.severity if status_code >= 400 else "INFO")
    payload = event_payload(
        code, status="failed" if status_code >= 400 else "success",
        user=user or noted.get("user"), session=session or noted.get("session"),
        request_id=request_id, detail=detail, exc=exc, meta=merged,
        http_status=status_code, location=_location(exc, depth=2))
    # A 4xx/5xx reads as its catalog wording, which is what the operator sees
    # in the toast anyway; a 2xx keeps the old access-line shape because that
    # line is scanned in bulk and "A request was handled." a thousand times
    # over tells nobody which route it was.
    text = entry.message if status_code >= 400 else \
        f"[HTTP] {method} {path} -> {status_code} ({int(took_ms)}ms)"
    logging.getLogger("cytron.access").log(
        _level_of(level), "%s", text, extra={EVENT_KEY: payload},
        exc_info=exc if exc is not None else None)


def code_for_status(status_code):
    """The catalog code a bare status maps to, for failures nobody labelled."""
    return {400: "API_BAD_REQUEST", 401: "AUTH_SESSION_EXPIRED",
            403: "AUTH_FORBIDDEN", 404: "API_NOT_FOUND",
            409: "API_CONFLICT", 429: "SEC_RATE_ABUSE",
            499: "API_CLIENT_DISCONNECTED", 502: "API_UPSTREAM_BAD_GATEWAY",
            503: "DB_UNAVAILABLE"}.get(int(status_code), "API_REQUEST"
                                       if int(status_code) < 400
                                       else "API_UNHANDLED")


def record_exception(where, exc, context=None):
    """Log an unhandled error with its context.

    Kept as-is for callers outside the request path (schedulers, the mail
    sender) that have no request id to join on. Inside a request, prefer
    `note_failure` + `log_request`, which produce one row instead of two.
    """
    logging.getLogger("cytron.error").error(
        "%s: %s (%s)", where, exc, context or {}, exc_info=exc,
        extra={EVENT_KEY: event_payload(
            "API_UNHANDLED", status="failed", exc=exc,
            request_id=(context or {}).get("rid"),
            user=(context or {}).get("user"),
            detail=f"{type(exc).__name__}: {exc}", meta=context)})


#: Defaults for a record key that is missing. Only the types matter here:
#: a table that gets `undefined` where it expected a number renders a blank
#: cell, and a sort on that column silently reorders the page.
_FIELD_DEFAULTS = {"ts": 0.0, "seq": 0, "line": 0, "http_status": 0,
                   "meta": None}


def as_record(row):
    """One row with every contracted key present, whatever wrote it.

    Rows reaching the page from ANOTHER process (core/services/log_collect.py
    reads the app's spool and each master's ring) are built by hand over
    there, against a format that is not this module's to change. Filling the
    gaps here rather than there keeps one definition of a record -- and means
    a collector that gains a field, or loses one, cannot half-render the
    table.

    `seq` and `run` are stamped by the process that WROTE the record, so a
    row from a process that does not stamp them arrives with seq 0 and an
    empty run. That is honest rather than exact: the page keys on
    source+run+seq when seq is set, and falls back for the rest.
    """
    out = dict(row or {})
    for field in RECORD_FIELDS:
        if out.get(field) in (None, ""):
            default = _FIELD_DEFAULTS.get(field, "")
            out[field] = {} if field == "meta" else default
    # REDACT HERE TOO, not only in RingHandler.emit.
    #
    # emit() covers records this process logged. A row collected from another
    # process was written as JSON by that process and read back here, so it
    # never passes through a handler -- and on 2026-09-23 that put a camera's
    # RTSP password on the Logs page in clear, from an app-process record
    # reading `camera 'rtsp://admin:<password>@192.168.1.65:554/stream1'`.
    # The page is admin-only, which is not the same as private: it is the page
    # people screenshot into a chat when they are asking for help.
    #
    # Redaction is idempotent, so running it over an already-masked local row
    # costs a scan and changes nothing.
    for field in ("message", "detail", "traceback"):
        if out.get(field):
            out[field] = redact(out[field])
    return out


def as_records(rows):
    return [as_record(row) for row in rows or ()]


def query(level=None, logger=None, contains=None, since=None, limit=200,
          category=None, event_id=None, status=None, source=None):
    """Newest-first slice of the ring. Every filter is optional."""
    wanted = LEVELS[LEVELS.index(level):] if level in LEVELS else None
    needle = (contains or "").lower()
    out = []
    for entry in reversed(_records):
        if wanted and entry["level"] not in wanted:
            continue
        if logger and not entry["logger"].startswith(logger):
            continue
        if category and entry.get("category") != category:
            continue
        if event_id and entry.get("event_id") != event_id:
            continue
        if status and entry.get("status") != status:
            continue
        if source and entry.get("source") != source:
            continue
        if since and entry["ts"] < float(since):
            continue
        # The id and the technical text are searched too: an operator with a
        # screenshot types "AI-5003", and a developer types the errno.
        if needle and needle not in (entry["message"] + " "
                                     + entry.get("event_id", "") + " "
                                     + entry.get("detail", "")).lower():
            continue
        out.append(entry)
        if len(out) >= int(limit or 200):
            break
    return out


def summary():
    """Counts per level plus the newest error -- what a status strip shows."""
    newest_error = next((e for e in reversed(_records)
                         if e["level"] in ("ERROR", "CRITICAL")), None)
    return {"counts": dict(_counts), "held": len(_records), "capacity": capacity(),
            "loggers": sorted({e["logger"] for e in _records}),
            "categories": sorted({e.get("category") for e in _records
                                  if e.get("category")}),
            "capture": capture_level(),
            "version": VERSION,
            # A page holding rows from before a restart can tell by the run
            # token changing, rather than by a sequence number going backwards
            # and being read as records arriving out of order.
            "source": _origin, "run": RUN_ID,
            "newest_error": newest_error, "now": time.time()}


def capture_levels():
    """Return levels currently available to the log filter."""
    return list(LEVELS[LEVELS.index(capture_level()):]) \
        if capture_level() in LEVELS else list(LEVELS)


def clear():
    """Empty this process's ring. Returns how many records it held, which
    the Logs page's Clear Log puts in its one trace record."""
    removed = len(_records)
    _records.clear()
    _pending.clear()
    for level in _counts:
        _counts[level] = 0
    return removed
