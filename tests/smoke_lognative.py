# One failure, one row -- and a badge that agrees with the table.
#
# WHAT WAS WRONG. Measured on a board: 6 CRITICAL rows for 3 real events, the
# same HailoRT drop listed five times over with one event id, one second and
# one message, while the strip above the table said "0 CRITICAL".
#
# Three separate causes, all of them in the collector:
#
#   * the native app.log scan gave its rows no identity at all (seq 0, empty
#     run). That was honest -- nothing WROTE those lines -- but it meant the
#     page keyed them on ts+message and could not recognise them across
#     polls, so every 2-second poll re-read the same 64KB tail and offered the
#     same lines again as new rows;
#   * the SAME failure arrived twice: once spooled as a real record through
#     Python logging, once scraped out of app.log. The scan exists for the
#     lines that never reach Python, not to re-report the ones that did;
#   * the badges came from the serving process's own ring counter, which is
#     cumulative since that process started and never decremented on
#     eviction. On the hub it therefore counted neither the page nor the ring.
#
# WHAT IS PINNED HERE.
#   * a scraped line keeps the same identity on a second scan, including
#     after the file has grown underneath it;
#   * a new native line is still a new row -- the fix must not dedupe away
#     the failure it exists to show;
#   * a scraped line is suppressed when a real record covers the same event,
#     with the time window wide enough for the scraped row's only timestamp
#     being the file's mtime;
#   * an uncovered scraped line survives, and a record for a DIFFERENT event
#     covers nothing;
#   * the counts equal the levels actually present in the merged set;
#   * ONE INCIDENT IS ONE ROW: HailoRT prints a paragraph per crash (three
#     lines for one service drop), and the operator read three identical
#     rows. The lines are folded into one row that keeps them all, and a
#     second crash further down the file is still its own row.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control                    # noqa: E402
from core.services import log_collect                         # noqa: E402

DATA = tempfile.mkdtemp()
APP_LOG = os.path.join(DATA, log_collect.APP_LOG_FILE)
SPOOL = os.path.join(DATA, log_collect.SPOOL_FILE)
NOW = time.time()

# The app's data dir is the app's own answer; the collector asks rather than
# guessing, so the test answers where the files actually are.
detector_control.data_dir = lambda systems_dir, slug: DATA

LOST = "[HailoRT] [error] CHECK failed - Lost communication with the server."
BUSY = "[HailoRT] [error] CHECK_SUCCESS failed with status=74"


def app_log(*lines):
    with open(APP_LOG, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def append(*lines):
    with open(APP_LOG, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def spool(*records):
    with open(SPOOL, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def keys(rows):
    """The identity the Logs page keys a row by: source + run + seq."""
    return [f"{r['source']}|{r['run']}|{r['seq']}" for r in rows]


def scan():
    return log_collect.native_records(APP_LOG, "app:line1")


# -- a scraped line is the same row on every poll -----------------------------
app_log("[INFO] starting", LOST, "[INFO] camera 0 opened")
first = scan()
assert len(first) == 1, first
assert first[0]["seq"] and first[0]["run"] == log_collect.NATIVE_RUN, first[0]
assert first[0]["meta"]["approx_time"] is True, \
    "the scraped time is the file's mtime and must still say so"

second = scan()
assert keys(first) == keys(second), \
    "the same scraped line came back with a different identity, so every " \
    "poll adds another copy of one failure to the page: " \
    + str(keys(first) + keys(second))
print("== a scraped line keeps its identity across polls")

# The file grows constantly underneath the scan -- an app writes INFO lines
# all day. The line already on the page must not be renamed by that.
append("[INFO] frame 1", "[INFO] frame 2")
grown = scan()
assert keys(grown) == keys(first), \
    "an unrelated line appended to app.log renamed the failure above it: " \
    + str(keys(grown) + keys(first))
print("== a growing app.log does not rename the rows already on the page")

# ...but a genuinely new native line IS a new row.
append(BUSY)
both = scan()
assert len(both) == 2, both
assert set(keys(first)) < set(keys(both)), \
    "a new native failure was folded into the old row: " + str(keys(both))
print("== a new native line is still a new row")

# -- one event is not two rows ------------------------------------------------
# The record the app spooled through Python logging for the SAME drop. Its
# timestamp is the real one, ten minutes before the scrape's -- app.log has
# been written to since, and the scraped row's only clock is the file mtime.
spool({"ts": NOW - 600, "level": "ERROR", "logger": "app.hailo",
       "message": "lost the accelerator service", "module": "hailo",
       "line": 214, "seq": 4, "run": "4d5fbd59",
       "event_code": "AI_SERVICE_CRASHED"})

rows = log_collect.app_records("systems", "line1")
crashed = [r for r in rows if r["event_code"] == "AI_SERVICE_CRASHED"]
assert len(crashed) == 1, \
    "one HailoRT failure produced two rows, the real record and the scraped " \
    "line: " + str([(r["seq"], r["run"], r["source"]) for r in crashed])
assert crashed[0]["seq"] == 4 and crashed[0]["run"] == "4d5fbd59", \
    "the SCRAPED half won -- the page kept the row with no real time, no " \
    "module and an approximate clock: " + str(crashed[0])
print("== a scraped line is suppressed by the real record of the same event")

# The other native line has no record behind it. That is the whole reason the
# scan exists, and it has to survive.
assert any(r["event_code"] == "AI_HAILO_BUSY" and r["run"]
           == log_collect.NATIVE_RUN for r in rows), \
    "the uncovered native line was suppressed too: " + str(rows)
print("== a native line no record covers is still reported")

# A record for a DIFFERENT event covers nothing.
spool({"ts": NOW, "level": "ERROR", "logger": "app.camera",
       "message": "camera '0' gave no frames", "module": "camera",
       "seq": 9, "run": "4d5fbd59", "event_code": "CAM_DISCONNECTED"})
rows = log_collect.app_records("systems", "line1")
assert sum(1 for r in rows if r["run"] == log_collect.NATIVE_RUN) == 2, \
    "an unrelated record swallowed the native lines: " + str(rows)
print("== an unrelated record suppresses nothing")

# A record far outside the window is a separate occurrence, not this one.
spool({"ts": NOW - log_collect.NATIVE_DUP_WINDOW_S - 3600, "level": "ERROR",
       "logger": "app.hailo", "message": "lost the accelerator service",
       "module": "hailo", "seq": 2, "run": "olderrun",
       "event_code": "AI_SERVICE_CRASHED"})
rows = log_collect.app_records("systems", "line1")
assert sum(1 for r in rows if r["event_code"] == "AI_SERVICE_CRASHED") == 2, \
    "an hour-old record hid a native line from today: " + str(rows)
print("== a record from long before the scrape does not cover it")


# -- the badges count what the page is showing --------------------------------
class State:
    """The slice of app.state the collector reads. Duck-typed on purpose:
    core services take state and config, never a framework object."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class System:
    def __init__(self, slug):
        self.slug = slug


spool({"ts": NOW - 600, "level": "ERROR", "logger": "app.hailo",
       "message": "lost the accelerator service", "module": "hailo",
       "seq": 4, "run": "4d5fbd59", "event_code": "AI_SERVICE_CRASHED"})

ring = [{"ts": NOW, "level": "INFO", "logger": "cytron.access",
         "message": "[HTTP] GET /api/logs -> 200 (3ms)"},
        {"ts": NOW - 1, "level": "WARNING", "logger": "core.services.masters",
         "message": "a master did not answer"}]
rows = log_collect.merge(list(ring), State(system=System("line1"),
                                           systems_dir="systems"))
counts = log_collect.level_counts(rows)

present = {}
for row in rows:
    present[row["level"]] = present.get(row["level"], 0) + 1
assert {k: v for k, v in counts.items() if v} == present, \
    "the badges and the table disagree -- badges " + str(counts) \
    + " against rows " + str(present)
assert sum(counts.values()) == len(rows), (counts, len(rows))
assert set(counts) == set(log_collect.LEVELS), \
    "the strip draws five buttons whatever happened, so all five levels must " \
    "be present even at zero: " + str(counts)
assert counts["CRITICAL"] or counts["ERROR"], \
    "the merged page holds a failed HailoRT event and the strip counted none"
print("== the counts equal the levels actually present in the merged set")

# -- one crash, one row -------------------------------------------------------
# The three lines the board actually printed for a single service drop. Same
# second, same source, same code, same catalog sentence: three identical rows
# on screen, which is what the operator reported as "it shows the error many
# times".
CRASH = [
    "[HailoRT] CHECK failed - Lost communication with the server.",
    "[HailoRT] CHECK_SUCCESS failed with status=HAILO_COMMUNICATION_CLOSED(62)",
    "[HailoRT] Failed to send request, status = HAILO_COMMUNICATION_CLOSED(62)",
]
app_log("[APP] frame 41", *CRASH, "[APP] ERROR: detection stopped")
rows = scan()
assert len(rows) == 1, \
    "one crash still draws " + str(len(rows)) + " rows: " \
    + str([r["detail"] for r in rows])
row = rows[0]
assert row["detail"].splitlines() == CRASH, \
    "the folded row lost a line, or listed them newest-first: " + row["detail"]
assert row["meta"]["repeats"] == 3, row["meta"]
assert "repeated" not in row["message"] and "x3" not in row["message"], \
    "the row says the accelerator dropped three times; it dropped once and " \
    "was described in three lines: " + row["message"]
print("== one crash printed as three lines is one row that keeps all three")

# A line the runtime printed over and over carries its own count rather than
# being listed again -- the app's own throttle vocabulary, not a second one.
app_log("[APP] frame 41", *(CRASH + [CRASH[-1]] * 48))
row = scan()[0]
assert row["meta"]["repeats"] == 51, row["meta"]
assert row["detail"].count("Failed to send request") == 1 \
    and "(x49)" in row["detail"], row["detail"]
print("== a line printed 49 times is counted, not listed 49 times")

# A burst that is still growing keeps its identity: the row updates in place
# instead of arriving as a new failure on every poll.
app_log("[APP] frame 41", *CRASH)
before = scan()[0]
append(CRASH[0])
after = scan()[0]
assert keys([before]) == keys([after]), \
    "another line of the SAME crash renamed the row: " \
    + str(keys([before]) + keys([after]))
assert after["meta"]["repeats"] == 4, after["meta"]
print("== a growing incident keeps its identity and raises its count")

# -- an undated file must not invent instants ---------------------------------
# The board that reopened this: nine HailoRT lines with byte gaps of 654, 825,
# 1175, 2721, 3655, 9558, 10473 and 30226. A byte-distance rule split them
# into six rows reading "2, 3, 1, 1, 1, 1", every one of them stamped with the
# file's mtime -- six crashes asserted in the same second, which is false
# whichever way it is read.
scattered = []
for n, gap in enumerate((654, 825, 1175, 2721, 3655, 9558, 10473, 30226)):
    scattered.append(CRASH[n % len(CRASH)])
    scattered += ["[APP] frame %d" % i for i in range(gap // 14)]
scattered.append(CRASH[0])
app_log(*scattered)
rows = scan()
assert len(rows) == 1, \
    "an app.log with no times split one repeatedly dropping accelerator " \
    "into " + str(len(rows)) + " rows, all claiming the same second: " \
    + str([r["meta"] for r in rows])
# Five of the nine: those gaps total nearly 60 KB, so the oldest lines are
# outside the 64 KB tail. That bound is deliberate (a fixed read on a busy
# card) and is not what this test is about.
assert rows[0]["meta"]["repeats"] == 5, rows[0]["meta"]
assert rows[0]["meta"]["approx_time"] is True, \
    "a row the file could not date must say so"
print("== a file with no times gives one row per code, whatever the gaps")

# -- two crashes are two rows, when the file can prove it ---------------------
# With the app's own time markers in the file, a scraped line is dated by the
# nearest marker above it, and an accelerator that drops twice in an evening
# says so. This is the ONLY thing allowed to split an incident.


def marker(offset_s=0):
    return time.strftime("[APP] ==== %Y-%m-%d %H:%M:%S ====",
                         time.localtime(NOW + offset_s))


app_log(*([marker(-900)] + CRASH + ["[APP] frame 1", marker(-600)] + CRASH))
rows = scan()
assert len(rows) == 2, \
    "two drops fifteen and ten minutes ago were folded into one row, so the " \
    "page cannot tell a board that dropped once from one that dropped " \
    "twice: " + str([r["meta"] for r in rows])
assert all(r["meta"]["repeats"] == 3 for r in rows), [r["meta"] for r in rows]
assert all(r["meta"]["approx_time"] is False for r in rows), \
    "a row dated from a real marker still claims its time is approximate: " \
    + str([r["meta"] for r in rows])
assert [int(r["ts"]) for r in rows] == [int(NOW) - 600, int(NOW) - 900], \
    "the rows are not sitting at the times the file gives them, newest " \
    "first: " + str([r["ts"] - NOW for r in rows])
print("== with times in the file, two crashes are two rows at two times")

# The same two bursts a minute apart are ONE incident: a fault that keeps
# printing is not a new fault every time it prints.
app_log(*([marker(-300)] + CRASH + ["[APP] frame 1", marker(-240)] + CRASH))
rows = scan()
assert len(rows) == 1 and rows[0]["meta"]["repeats"] == 6, \
    "a fault that printed again a minute later became a second incident: " \
    + str([r["meta"] for r in rows])
assert rows[0]["meta"]["span_s"] == 60, rows[0]["meta"]
print("== the same fault printing a minute later is the same incident")

# A different failure in the same burst is its own row: a lost service and a
# busy chip are two different things to do something about.
app_log(*(CRASH + [BUSY]))
rows = scan()
assert sorted(r["event_code"] for r in rows) \
    == ["AI_HAILO_BUSY", "AI_SERVICE_CRASHED"], [r["event_code"] for r in rows]
print("== two different failures in one burst stay two rows")

# A dense burst is one incident, however much it prints. 20 KB of the same
# failure back to back must not come out as ten rows, or a heavily-printing
# fault reads as a dozen faults and we are back where we started.
app_log(*(CRASH * 100))
rows = scan()
assert len(rows) == 1, \
    "a continuous burst fragmented into " + str(len(rows)) + " rows, one " \
    "per window, so an incident that prints heavily reads as many incidents"
assert rows[0]["meta"]["repeats"] == 300, rows[0]["meta"]
assert rows[0]["detail"].count("\n") == 2 and "(x100)" in rows[0]["detail"], \
    rows[0]["detail"]
print("== a long continuous burst is one row, not one row per window")

# -- a log outlives the process that wrote it ---------------------------------
# app.log is append-only across runs, so a board started and stopped for
# months holds every run's output in one file. Two systems on the board carried
# 26,240 and 4,155 HailoRT lines in logs last written three weeks earlier --
# start either one and the scan served September's crash, dated today.
MARK = "[APP] ==== app started 2026-09-01 02:11:04 ===="
app_log(*(CRASH + [MARK, "[APP] frame 1", "[APP] frame 2"]))
assert scan() == [], \
    "lines from a run that has ENDED were scraped and stamped with today's " \
    "mtime: " + str([r["detail"] for r in scan()])
print("== nothing above the run marker is scraped")

append(*CRASH)
rows = scan()
assert len(rows) == 1 and rows[0]["meta"]["repeats"] == 3, \
    "the CURRENT run's crash was lost with the previous run's: " + str(rows)
print("== this run's own lines are scraped as normal")

# An app package nobody has redeployed yet writes no marker. A recent log
# without one is still scraped in full -- that is the case the scan exists
# for, and losing it would be worse than the staleness it prevents.
app_log(*(["[APP] frame 1"] + CRASH))
assert len(scan()) == 1, "a marker-less log stopped being scraped at all"
print("== a marker-less log with a recent mtime is still scraped")

# A log nobody has written to for weeks is history, whatever is in it.
stale = time.time() - log_collect.NATIVE_STALE_S - 60
os.utime(APP_LOG, (stale, stale))
assert scan() == [], \
    "a three-week-old app.log still produced rows, which is the stale " \
    "camera banner in a new place"
print("== a log nobody has written to for a day is not scraped")

# ...and neither is one older than the run supposedly writing it.
app_log(*CRASH)
assert log_collect.native_records(APP_LOG, "app:line1",
                                  started_at=time.time() + 5) == [], \
    "a log untouched since this run started was scraped as this run's output"
assert log_collect.native_records(APP_LOG, "app:line1", started_at=1.0), \
    "a log written after the run started was refused"
print("== a log older than the running process is not scraped")

# The wiring: app_records must ASK the app when it started, or none of the
# above reaches the page. BUSY is in the log because the spool still holds a
# record covering the crash, and a covered line is suppressed for a different
# reason entirely -- this has to fail for the reason it says it does.
app_log(*(CRASH + [BUSY]))
detector_control.app_state = lambda systems_dir, slug: {"started_at":
                                                       time.time() + 60}
scraped = [r for r in log_collect.app_records("systems", "line1")
           if r["run"] == log_collect.NATIVE_RUN]
assert not scraped, \
    "app_records scraped a log that predates the run it belongs to -- the " \
    "start time is not being read: " + str(scraped)
detector_control.app_state = lambda systems_dir, slug: {}
assert [r for r in log_collect.app_records("systems", "line1")
        if r["run"] == log_collect.NATIVE_RUN], \
    "a system with no state file at all lost its native lines"
print("== app_records reads the app's own start time before it scrapes")

print("ALL OK")
