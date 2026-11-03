# One failure, one record -- and that record says where it happened.
#
# WHAT WAS WRONG. A single failing request wrote TWO lines. The access
# middleware wrote "[HTTP] POST /api/folders -> 503 (41ms) rid=deadbeef" and
# the error handler wrote "POST /api/folders: database unavailable". The
# operator saw the same event twice, the route was on one line, the cause was
# on the other, and neither had the user, the session, the version or the
# place in the code it came from -- even though `record.funcName` and
# `record.pathname` arrive free on every LogRecord and were simply being
# dropped on the floor, and `request.state.session_sid` had existed since
# sessions shipped without ever being logged once.
#
# WHAT IS PINNED HERE.
#
#   * ONE failure produces exactly ONE ring record, on both paths -- the
#     handled one (the handler notes, the middleware emits) and the unhandled
#     one (the middleware notes, the 500 handler emits). The asymmetry is the
#     bug waiting to come back: emit in both places and the duplicate
#     returns, emit in neither and the failure disappears.
#
#   * the record carries the function, the file, the line and the version,
#     and the location is the RAISE site, not the middleware that caught it.
#
#   * the user-facing message and the technical detail stay in separate
#     fields, so the panel can show the first and hide the second.
#
#   * the ring stays bounded and in memory. That property is the whole reason
#     it exists: the moment logs matter most is the moment the database is
#     down, and a log table cannot be read then.
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import log_catalog as catalog             # noqa: E402
from core.services import logs as logs_service               # noqa: E402
from core.version import VERSION                             # noqa: E402

logs_service.install()

#: Every column the Logs page reads off a row. Written out here as well as
#: in the service, because this list IS the contract handed to the frontend
#: and a field quietly going missing renders as a blank cell nobody notices.
#:
#: The spellings are rulings, not preferences: `meta` not `metadata`,
#: `version` not `app_version`, `source` for the ORIGIN not "module:lineno",
#: and `http_status` in exactly one place rather than also as
#: `meta.status_code`.
COLUMNS = ("ts", "seq", "run", "level", "logger", "source", "message",
           "detail", "event_id", "event_code", "category", "status", "action",
           "module", "function", "file", "line", "user", "session", "version",
           "request_id", "http_status", "meta")


def only_record():
    """The ring's single entry, or a readable failure saying how many there
    really were -- the count IS the thing under test."""
    held = logs_service.query(limit=50)
    assert len(held) == 1, (
        f"one failure produced {len(held)} records, not 1:\n"
        + "\n".join(f"  [{e['level']}] {e['logger']}: {e['message'][:90]}"
                    for e in held))
    return held[0]


# -- every field is present on an ordinary line --------------------------------
#
# Not just on the enriched ones: a plain logger.info() from anywhere in the
# product still has to land as a complete row, or half the page is blank.
logs_service.clear()
logging.getLogger("core.services.sync").info("an ordinary line")
row = only_record()
missing = [c for c in COLUMNS if c not in row]
assert not missing, f"a plain log line is missing columns: {missing}"
extra = [c for c in row if c not in COLUMNS and c != "traceback"]
assert not extra, (
    f"the record grew keys the contract does not name: {extra}. Ying builds "
    f"the table from this list; an unannounced key is a column nobody draws.")
assert set(COLUMNS) == set(logs_service.RECORD_FIELDS), (
    "RECORD_FIELDS and this test disagree: "
    f"only in the service: {sorted(set(logs_service.RECORD_FIELDS) - set(COLUMNS))}, "
    f"only in the test: {sorted(set(COLUMNS) - set(logs_service.RECORD_FIELDS))}")

# The spellings that were nearly shipped in two forms each.
for banned in ("metadata", "app_version", "status_code", "src"):
    assert banned not in row, (
        f"{banned!r} is back on the record. One name per field: the page "
        f"reading both spellings is how a blank column ships.")
assert row["version"] == VERSION, (row["version"], VERSION)
assert row["function"] and row["file"] and row["line"], \
    "funcName/pathname/lineno are being thrown away again: " + repr(row)
assert row["file"].endswith("tests/smoke_logrecord.py"), row["file"]
assert row["status"] == "success", \
    "an INFO line defaulted to failed, so the status filter is meaningless"
assert row["source"] == "hub", (
    "`source` must name the PROCESS (hub / master:<slug> / app:<slug>). It "
    "used to hold module:lineno, which duplicated the module and line fields "
    "below it in a worse format, and forced the page to tell the two "
    "meanings apart with a regex on the shape: " + repr(row["source"]))
print("== an ordinary log line lands with every column filled in")

# -- source names the process, in the collector's own vocabulary --------------
#
# ONE vocabulary, not two. core/services/log_collect.py stamps these exact
# strings on records collected from the other processes on a board, and a
# second spelling here would split the page's source filter clean in half --
# the near-miss that ships as a blank column.
from core.services import log_collect                        # noqa: E402

assert logs_service.SOURCE_HUB == "hub"
assert logs_service.source_of_master("line1") == "master:line1"
assert logs_service.source_of_app("line1") == "app:line1"
assert log_collect.app_source("line1") == logs_service.source_of_app("line1"), \
    "the collector and the record disagree about what an app is called"


class _FakeState:
    class system:
        slug = "line1"


assert log_collect.local_source(_FakeState()) == \
    logs_service.source_of_master("line1"), \
    "the collector and the record disagree about what a master is called"
assert log_collect.local_source(object()) == logs_service.SOURCE_HUB

# ...and the collector puts it under `source`, not `src`.
tagged = log_collect._tag([{"ts": 1.0, "message": "x"}], "master:line1")
assert tagged[0].get("source") == "master:line1", tagged
assert "src" not in tagged[0], (
    "the collector still emits `src` while the page reads `source`. One "
    "name: " + repr(tagged[0]))

logs_service.set_origin(logs_service.source_of_master("line1"))
logs_service.clear()
logging.getLogger("core.services.sync").info("from the master")
assert only_record()["source"] == "master:line1", \
    "set_origin did not reach the records, so a master stamps its rows as " \
    "the hub's and the source filter sends support to the wrong process"
logs_service.set_origin(logs_service.SOURCE_HUB)
print("== source names the process, in the same words the collector uses")

# -- seq is exact where ts|message was only nearly exact -----------------------
#
# The page keys rows to dedupe across refreshes and to count what arrived
# while it was paused, and it was doing both on ts|message. Two records in
# the same millisecond with the same text are not hypothetical: a retry loop
# produces exactly that, and "very nearly exact" is the worst kind.
logs_service.clear()
retry = logging.getLogger("core.services.sync")
for _ in range(50):
    retry.warning("connection refused, retrying")    # identical text, one ms
rows = logs_service.query(limit=50)
assert len({r["seq"] for r in rows}) == 50, \
    "seq repeated inside one process run, so the page cannot dedupe on it"
seqs = [r["seq"] for r in reversed(rows)]
assert seqs == sorted(seqs), "seq is not monotonic, so it cannot order rows"
assert len({r["run"] for r in rows}) == 1, "the run token changed mid-process"
assert logs_service.summary()["run"] == rows[0]["run"], \
    "the run token on the record and the one in the summary disagree"
assert logs_service.summary()["source"] == "hub"
print("== seq is unique and monotonic within the run, and the page can read "
      "the run token back")

# seq RESETS to 1 when the process restarts. It is a per-process counter and
# cannot be anything else -- three processes on a board each count their own
# and none can see the others'. `run` is what keeps the key exact across that
# reset, and the detection app is the case that proves it is needed: it
# re-execs itself when the accelerator service drops it, and its spool file
# survives the restart holding BOTH runs' records.
assert len(logs_service.RUN_ID) >= 8, logs_service.RUN_ID
assert logs_service.summary()["run"] == logs_service.RUN_ID
print("== source + run + seq stays exact across a restart, where seq alone "
      "does not")

# -- the handled failure: the handler notes, the middleware emits --------------
#
# The order the real code runs in: an HTTPException is turned into a response
# INSIDE the access middleware, so the handler has already left its half
# behind by the time the middleware writes the row.
logs_service.clear()
logs_service.note_failure("deadbeef", code="DB_UNAVAILABLE",
                          detail="could not connect to server: "
                                 "postgres://cytron:hunter2@127.0.0.1:5432/iriv",
                          user="rina", session="sid-77")
logs_service.log_request("PATCH", "/api/folders/3", 503, 41.2,
                         request_id="deadbeef")
row = only_record()
assert row["event_id"] == "DB-7001", row
assert row["category"] == "Database", row
assert row["status"] == "failed", row
assert row["level"] == "ERROR", row
assert row["request_id"] == "deadbeef", row
assert row["user"] == "rina" and row["session"] == "sid-77", \
    "the user and the session did not survive the join: " + repr(row)
assert row["meta"]["method"] == "PATCH", row["meta"]
assert row["meta"]["path"] == "/api/folders/3", row["meta"]
assert row["http_status"] == 503, row
assert "status_code" not in row["meta"], (
    "http_status shipped twice, as a field AND as meta.status_code. Two "
    "spellings means the details panel reads the one nobody fills in: "
    + repr(row["meta"]))
print("== a handled failure: the handler's half and the request's half land "
      "as one row")

# The operator's half and the engineer's half are separate fields.
assert row["message"] == catalog.CATALOG["DB_UNAVAILABLE"].message, row["message"]
assert "postgres" not in row["message"], \
    "the DSN reached the operator-facing message"
assert "5432" in row["detail"], \
    "the technical text was dropped, so nothing can diagnose this row"
assert "hunter2" not in (row["detail"] + row["message"]), \
    "the DSN password reached the Logs page:\n" + row["detail"]
assert row["action"] == catalog.CATALOG["DB_UNAVAILABLE"].action, row["action"]
print("== the wording the operator reads and the text that diagnoses the "
      "fault are different fields, and the password is masked in both")

# -- the unhandled failure: the middleware notes, the 500 handler emits --------
logs_service.clear()


def a_route_that_fails():
    """Stands in for the raise site; the record must point HERE, not at the
    middleware or the handler that caught it."""
    raise RuntimeError("psycopg2 could not connect")


try:
    a_route_that_fails()
except RuntimeError as exc:
    # middleware: notes on its way past, writes nothing
    logs_service.note_failure("cafe1234", took_ms=88.0, user="rina",
                              session="sid-77")
    assert not logs_service.query(limit=5), \
        "the access middleware wrote a line of its own -- that is the " \
        "duplicate, back again"
    # 500 handler: emits the one row
    logs_service.log_request("POST", "/api/events", 500, request_id="cafe1234",
                             code="API_UNHANDLED", exc=exc,
                             detail=f"{type(exc).__name__}: {exc}")

row = only_record()
assert row["event_id"] == "API-6040", row
assert row["status"] == "failed" and row["level"] == "ERROR", row
assert row["function"] == "a_route_that_fails", (
    "the record points at the catch site, not the raise site: "
    + repr(row["function"]))
assert row["file"].endswith("tests/smoke_logrecord.py"), row["file"]
assert row["line"] > 0, row
assert row["version"] == VERSION, row
assert row.get("traceback"), "no traceback was captured for a 500"
assert row["meta"]["duration_ms"] == 88.0, \
    "the duration the middleware noted was lost: " + repr(row["meta"])
assert row["meta"]["exception"] == "RuntimeError", (
    "the exception type is not on the record, so grouping failures by it "
    "means parsing prose out of `detail`: " + repr(row["meta"]))
assert row["http_status"] == 500, row
assert row["user"] == "rina" and row["session"] == "sid-77", row
print("== an unhandled failure: one row, pointing at the code that raised, "
      "carrying the middleware's timing")

# The pending slot must not survive the flush, or the next request with a
# recycled id inherits somebody else's exception.
assert not logs_service.take_failure("cafe1234"), \
    "the noted facts were not consumed, so they will leak onto another row"
print("== the pending half is consumed when the row is written")

# -- a success is still one line, and still readable in bulk -------------------
logs_service.clear()
logs_service.log_request("GET", "/api/devices", 200, 12.0, request_id="aaaa1111",
                         user="rina")
row = only_record()
assert row["status"] == "success" and row["level"] == "INFO", row
assert "/api/devices" in row["message"], (
    "a 2xx stopped naming its route. The access log is scanned in bulk and "
    "'A request was handled.' a thousand times over tells nobody anything: "
    + row["message"])
print("== a successful request is one line that still names its route")

# -- a failure nobody labelled still gets an id --------------------------------
#
# 344 raise sites have not been converted yet. Every one of them still has to
# produce a categorised row, or the page has a hole in it for months.
logs_service.clear()
logs_service.log_request("GET", "/api/media/nope", 404, 3.0, request_id="bbbb2222")
row = only_record()
assert row["event_id"] == "API-6011" and row["category"] == "API", row
assert row["status"] == "failed", row
print("== an unconverted raise site still lands with an id and a category")

# -- the ring stays bounded, and in memory -------------------------------------
logs_service.clear()
log = logging.getLogger("core.services.sync")
for i in range(logs_service.MAX_RECORDS + 250):
    log.info("filler %d", i)
assert logs_service.summary()["held"] <= logs_service.capacity(), \
    "the ring is no longer bounded -- this runs for months on a Pi"
assert logs_service.capacity() == logs_service.MAX_RECORDS
newest = logs_service.query(limit=1)[0]
assert newest["message"].endswith(str(logs_service.MAX_RECORDS + 249)), \
    "the ring dropped the NEWEST records instead of the oldest"
print(f"== the ring holds the newest {logs_service.capacity()} and no more")

# Nothing here may need a database. The point of the ring is that it still
# works during the outage that is being logged.
assert "core.db" not in sys.modules, \
    "the log ring pulled in core.db -- it must keep working with Postgres " \
    "down, which is exactly when an operator opens this page"
print("== the ring reached none of this through the database")

# -- the search box finds a row by the code on a screenshot --------------------
logs_service.clear()
logs_service.note_failure("ffff9999", code="AI_SERVICE_UNAVAILABLE",
                          detail="[Errno 111] Connection refused on :8101")
logs_service.log_request("GET", "/video_feed/0", 503, 5.0, request_id="ffff9999")
assert logs_service.query(contains="AI-5003"), \
    "an operator typing the code from their screenshot finds nothing"
assert logs_service.query(contains="Errno 111"), \
    "a developer cannot search the technical text"
assert logs_service.query(category="AI Inference"), "the category filter misses"
assert logs_service.query(status="failed"), "the status filter misses"
assert not logs_service.query(category="Storage"), \
    "the category filter matches rows it should not"
print("== a row is findable by its id, by its technical text, by category "
      "and by status")

# -- the ordering contract, in the source --------------------------------------
#
# The behaviour above is simulated; this makes sure the two real call sites
# are still wired the way the simulation assumes. Get the asymmetry backwards
# and the duplicate line comes straight back, silently.
import io                                                    # noqa: E402

mw = io.open(os.path.join(ROOT, "core", "middleware.py"),
             encoding="utf-8").read()
inner = mw[mw.index("except Exception:"):mw.index("raise\n")]
assert "note_failure" in inner and "log_request" not in inner, (
    "the access middleware writes its own line on the exception path again -- "
    "the 500 handler runs OUTSIDE it and emits the real row, so this one is "
    "a duplicate with none of the cause on it")
tail = mw[mw.index("took_ms = "):]
assert "log_request" in tail, \
    "the access middleware no longer emits the row for a handled failure, so " \
    "an HTTPException now logs nothing at all"

handlers = io.open(os.path.join(ROOT, "core", "routes", "log_errors.py"),
                   encoding="utf-8").read()
http_error = handlers[handlers.index("async def _http_error"):
                      handlers.index("async def _client_gone")]
assert "note_failure" in http_error, \
    "the HTTPException handler emits instead of noting, which duplicates " \
    "every 4xx the access middleware also writes"
unhandled = handlers[handlers.index("async def _unhandled"):]
assert "log_request" in unhandled and "note_failure" not in unhandled, \
    "the 500 handler notes instead of emitting -- nothing downstream of it " \
    "will ever write the row"
assert "session_sid" in handlers, \
    "the session id is being dropped again; it is on the request and has " \
    "never cost anything to log"
print("== the middleware notes and the 500 handler emits; the 4xx handler is "
      "the other way round")

print("ALL OK")
