# A failure inside the detection app has to reach the Logs page.
#
# WHAT WAS WRONG, on a live board. The hub's Logs page held exactly two
# loggers -- core.services.masters and cytron.access -- while the same board's
# app.log held
#
#     [HailoRT] [error] CHECK failed - Lost communication with the server.
#     [HailoRT] [error] CHECK_SUCCESS failed with status=HAILO_COMMUNICATION_CLOSED(62)
#
# and nobody could see it. Cameras, the accelerator, detection and alert
# sending all run in the app process, which is spawned per system, had no
# logging handler at all, and reported with print() into a file no route
# reads. The one thing an operator opens that page for was the one thing it
# could not show.
#
# WHAT IS PINNED HERE.
#   * a failure in the app process becomes a structured record on disk, with
#     the catalog code that says what KIND of failure it was;
#   * that record comes back out through the collector with its source
#     attached (app:<slug>), because a page holding three processes' rows is
#     useless if you cannot tell which one spoke;
#   * the catalog owns the wording and the severity, and the app's own
#     technical text survives as `detail` -- both halves, one row;
#   * progress chatter does NOT reach the spool, or a board writes to its SD
#     card all day to say everything is fine;
#   * a repeating failure is written once per window, with the repeats
#     counted, for the same reason;
#   * every spooled record is stamped with `seq` and `run`, and the pair is
#     what survives the app re-execing itself: the spool outlives the run that
#     wrote it, so seq alone would collide across the restart -- and the
#     restart is the incident anybody is reading this file for;
#   * a thread that dies is recorded as a stopped TASK, not as the whole
#     process exiting, because those send an operator to two different jobs;
#   * app.log still exists and still gets the lines, because that file is what
#     people SSH for today and removing it mid-change is a regression;
#   * HailoRT's native lines -- printed from C++ to fd 2, never through Python
#     logging -- are picked out of app.log's tail, since that is the only
#     place they ever exist.
import itertools
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import faults                                        # noqa: E402
from core.services import log_catalog as catalog              # noqa: E402
from core.services import log_collect                         # noqa: E402

DATA = tempfile.mkdtemp()
APP_LOG = os.path.join(DATA, "app.log")
SPOOL = faults.spool_path(DATA)

# The app is spawned with its stdout pointed AT app.log (app/runtime.py
# start()), so this is the same arrangement the board runs: install() takes
# whatever stdout is and keeps writing there.
_real_stdout = sys.stdout
sys.stdout = open(APP_LOG, "w", encoding="utf-8", buffering=1)
faults.install(DATA)
faults.install_hooks("the detection app")

log = faults.logger("app.camera")


def spooled():
    """Every record in the spool, oldest first."""
    if not os.path.exists(SPOOL):
        return []
    with open(SPOOL, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def app_log_text():
    sys.stdout.flush()
    with open(APP_LOG, "r", encoding="utf-8") as handle:
        return handle.read()


def say(line):
    print(line, file=_real_stdout)


# -- a real failure, with its code --------------------------------------------
log.error("camera '2' is held by another system on this box (pid 991)",
          extra=faults.event(faults.CAM_BUSY, camera="cam1"))

records = spooled()
assert len(records) == 1, f"the app spooled {len(records)} records, wanted 1"
assert records[0]["event_code"] == "CAM_BUSY", records[0]
assert "pid 991" in records[0]["message"], records[0]
assert records[0]["logger"] == "app.camera", records[0]
assert records[0]["meta"].get("camera") == "cam1", records[0]
say("== an app-process failure lands in the spool, with its catalog code")

# -- every record is stamped with who wrote it and in which order -------------
assert records[0]["seq"] == 1, records[0]
assert records[0]["run"] == faults.RUN_ID and records[0]["run"], records[0]
assert records[0]["module"] == "smoke_appfaults", records[0]
assert int(records[0]["line"]) > 0, records[0]
# `source` on this contract names the PROCESS that noticed the failure, and
# only the master reading this file knows which system that is. An app that
# wrote its own would be writing a guess.
assert "source" not in records[0],     "the spool claimed a source -- that field names the process, and the "     "app cannot know its own system's slug: " + str(records[0])
say("== and it carries seq, run, module and line -- but never its own source")

# ...and it reached app.log too, which is what an SSH session reads.
assert "pid 991" in app_log_text(), app_log_text()
assert "[APP] ERROR:" in app_log_text(), \
    "app.log no longer marks a failure as one:\n" + app_log_text()
say("== and the same line is still in app.log, where people look for it today")

# -- progress chatter stays out of the spool ----------------------------------
log.info("camera '2': device rate 30 fps")
assert len(spooled()) == 1, \
    "an INFO line reached the spool -- a healthy board would write to its " \
    "SD card all day"
assert "device rate 30 fps" in app_log_text()
say("== progress chatter goes to app.log only, never to the card as a record")

# -- a failure that repeats every frame is written once per window ------------
# The field window (30s) while the 50 repeats go in, so a loaded machine that
# stalls this process mid-loop cannot run the window out and write a third
# record; then a zero window stands in for "the window has passed" instead of
# sleeping past a short one.
faults.REPEAT_S = 30.0
for _ in range(50):
    log.error("camera '2' gave no frames (%s)", "select() timeout",
              extra=faults.event(faults.CAM_STREAM_STALLED))
assert len(spooled()) == 2, \
    f"a failure repeating 50x wrote {len(spooled())} records -- the throttle " \
    "is what keeps this off an SD card"
faults.REPEAT_S = 0.0                       # the window is over
log.error("camera '2' gave no frames (%s)", "select() timeout",
          extra=faults.event(faults.CAM_STREAM_STALLED))
faults.REPEAT_S = 0.4                       # short again for what follows
newest = spooled()[-1]
assert "repeated 49x" in newest["message"], \
    "the suppressed repeats were not counted, so the page under-reports a " \
    "camera failing every frame: " + newest["message"]
say("== a failure repeating every frame writes once a window, repeats counted")

# -- a thread that dies is not allowed to die quietly -------------------------
before = len(spooled())


def boom():
    raise RuntimeError("the modbus bus vanished")


worker = faults.spawn("modbus-fn3", boom)
worker.start()
worker.join(5)
died = spooled()[before:]
assert died, "a thread raised out of its target and nothing was recorded"
assert died[-1]["event_code"] == "SYS_TASK_STOPPED",     "a dead worker was filed as the whole process exiting, which sends an "     "operator to restart a board that is still detecting: " + str(died[-1])
assert "modbus-fn3" in died[-1]["message"], died[-1]
assert "the modbus bus vanished" in (died[-1].get("traceback") or ""), died[-1]
say("== a worker thread that raises is recorded, named, with its traceback")

# A loop that RETURNS is the other half, and the one threading.excepthook can
# never see: the feature is just as stopped, and the page just as wrong.
before = len(spooled())
quiet = faults.spawn("api-fn7", lambda: None)
quiet.start()
quiet.join(5)
returned = spooled()[before:]
assert returned and "returned" in returned[-1]["message"], \
    "a long-lived loop returned and nothing said the feature had stopped"
say("== and a loop that simply returns is recorded too")

# Work that is MEANT to finish must not cry wolf every time it does.
before = len(spooled())
done = faults.spawn("event-archiver", lambda: None, once=True)
done.start()
done.join(5)
assert len(spooled()) == before, \
    "a one-shot thread reported its own normal completion as a fault"
say("== one-shot work finishing is not reported as a death")

# -- threading.excepthook covers the threads nobody wrapped -------------------
before = len(spooled())
raw = threading.Thread(target=boom, name="unwrapped-loop")
raw.start()
raw.join(5)
hooked = spooled()[before:]
assert hooked and "unwrapped-loop" in hooked[-1]["message"], \
    "a bare threading.Thread died and the global hook did not record it"
say("== the global thread hook catches loops nobody wrapped")

# -- the collector: a record comes back with its source and its wording -------
rows = log_collect.read_spool(SPOOL, "app:line1", limit=100)
assert rows, "the collector read nothing out of the spool"
busy = next(r for r in rows if r["event_code"] == "CAM_BUSY")
assert busy["source"] == "app:line1", busy
entry = catalog.CATALOG["CAM_BUSY"]
assert busy["message"] == entry.message, \
    "the app's own sentence reached the page instead of the catalog's: " \
    + busy["message"]
assert "pid 991" in busy["detail"], \
    "the technical text was dropped -- it is the only string that diagnoses " \
    "anything: " + str(busy)
assert busy["level"] == entry.severity, busy
assert busy["category"] == entry.category and busy["event_id"] == entry.event_id
assert busy["action"] == entry.action, busy
say("== a spooled record comes back as a full page row: source, code, wording")

# Newest first, like every other page of logs.
assert rows == sorted(rows, key=lambda r: r["ts"], reverse=True)
say("== the collector answers newest first")

assert busy["seq"] > 0 and busy["run"] == faults.RUN_ID, busy
assert busy["module"] == "smoke_appfaults" and busy["line"] > 0, busy
say("== seq, run, module and line survive the crossing")

# -- the restart this whole file exists for -----------------------------------
#
# The app re-execs itself when the accelerator service drops it. Same pid,
# same spool, and the counter starts again at 1 -- so the second run's records
# carry sequence numbers the first run already used. A page deduping on
# source+seq would drop precisely the records written after the crash.
first_run = faults.RUN_ID
faults.RUN_ID = "restart2"                  # what a fresh process start does
faults._seq = itertools.count(1)
# A fresh process has no throttle memory; this one logged the same CAM_BUSY
# line moments ago. A zero window lets it through, as the restart would --
# rather than depending on the test having taken longer than REPEAT_S to get
# here (it used to, by way of a sleep further up).
faults.REPEAT_S = 0.0
log.error("camera '2' is held by another system on this box (pid 991)",
          extra=faults.event(faults.CAM_BUSY, camera="cam1"))
faults.REPEAT_S = 0.4

rows = log_collect.read_spool(SPOOL, "app:line1", limit=200)
runs = {r["run"] for r in rows}
assert first_run in runs and "restart2" in runs, runs
collided = [r for r in rows if r["run"] == "restart2" and r["seq"] == 1]
twins = [r for r in rows if r["seq"] == 1]
assert len(collided) == 1 and len(twins) == 2,     "the restart did not reuse a sequence number, so this test is no longer "     "testing what it says it is"
keys = {(r["source"], r["run"], r["seq"]) for r in rows}
assert len(keys) == len(rows),     "source+run+seq is not unique across a restart -- the records written "     "after the crash are the ones a page would drop"
say("== two runs in one spool stay distinct: seq repeats, run does not")
faults.RUN_ID = first_run

# A writer that stamps neither arrives empty rather than invented: a made-up
# sequence number is one the page would dedupe on.
legacy = log_collect._expand(
    {"ts": time.time(), "level": "ERROR", "logger": "app.camera",
     "message": "camera '0' gave no frames", "source": "camera:1309",
     "event_code": "CAM_DISCONNECTED"}, "app:line1")
assert legacy["seq"] == 0 and legacy["run"] == "", legacy
assert legacy["module"] == "camera" and legacy["line"] == 1309,     "a spool from an app package nobody has redeployed yet lost its module "     "and line: " + str(legacy)
say("== an unstamped record from an older app package still reads, empty not faked")

# -- HailoRT's native lines, which never touch Python logging -----------------
with open(APP_LOG, "a", encoding="utf-8") as handle:
    handle.write("[HailoRT] [error] CHECK failed - Lost communication with "
                 "the server.\n")
    handle.write("[HailoRT] [error] CHECK_SUCCESS failed with "
                 "status=HAILO_COMMUNICATION_CLOSED(62)\n")
    handle.write("[HailoRT] [error] CHECK failed - Lost communication with "
                 "the server.\n")
native = log_collect.native_records(APP_LOG, "app:line1")
assert native, \
    "the line the whole ticket was about is still visible to nobody"
assert all(r["event_code"] == "AI_SERVICE_CRASHED" for r in native), native
# One drop, one row. HailoRT prints a paragraph per failure -- these three
# lines are one service drop -- and this used to be two rows (one per distinct
# line, identical copies counted). Both read on screen as the same second, the
# same event id and the same catalog sentence, which the operator reported as
# the error being shown many times. The lines are all kept, in `detail`.
assert len(native) == 1, \
    f"one service drop still draws {len(native)} rows"
assert "COMMUNICATION_CLOSED" in native[0]["detail"], native
assert native[0]["detail"].count("Lost communication") == 1 \
    and "(x2)" in native[0]["detail"], \
    "the repeated line was listed twice instead of counted: " \
    + native[0]["detail"]
assert native[0]["meta"]["repeats"] == 3, native[0]["meta"]

# -- and app.log can say what time it is --------------------------------------
# HailoRT writes C++ to fd 2 and will never carry a timestamp, so the only
# thing that can date a scraped line is a line above it that has one. Without
# that, every scraped row took the FILE's mtime and six crashes spread over an
# afternoon all reached the page claiming the same second.
marks = [line for line in app_log_text().splitlines()
         if log_collect.NATIVE_TIME_RE.match(line)]
assert marks, \
    "app.log carries no time marker at all, so nothing in it can be dated:\n" \
    + app_log_text()[:800]
assert "[APP] ERROR:" in app_log_text() and "device rate 30 fps" in app_log_text(), \
    "the marker changed the shape of the lines around it"
# One a minute, not one per line: the file is read by people and capped at
# 16 MB, and a stamp on every line costs a fifth of that history.
assert len(marks) == 1, \
    f"{len(marks)} markers for a test that logs in one second -- app.log is " \
    "being stamped far more often than STAMP_EVERY_S allows"
dated = log_collect.native_records(APP_LOG, "app:line1")
assert abs(dated[0]["ts"] - time.time()) < 120, \
    "the row was dated from something other than the marker just written: " \
    + str(dated[0]["ts"] - time.time())
say("== app.log carries a time marker, and a scraped line is dated from it")
assert native[0]["meta"].get("approx_time") is False, \
    "a native line has no timestamp of its own, but the marker above it " \
    "does -- and a row dated from a real marker must stop calling its time " \
    "approximate, or nothing on the page can be trusted to be exact: " \
    + str(native[0]["meta"])
say("== HailoRT's own error lines are picked out of app.log and catalogued")

# -- and the file people SSH for is still there -------------------------------
assert os.path.exists(APP_LOG), "app.log is gone -- that is a regression"
assert os.path.getsize(APP_LOG) > 0, "app.log exists but nothing writes to it"
assert "[APP]" in app_log_text(), \
    "app.log no longer carries the [APP] tag people grep for"
say("== app.log still exists, still written, still grep-able")

sys.stdout.close()
sys.stdout = _real_stdout
print("ALL OK")
