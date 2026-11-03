# The Logs page shows its own process: a system its master and app, the hub
# the hub (Tok Aba, 2026-10-14). History of why the app half exists below.
#
# WHAT WAS WRONG. A board runs three kinds of process -- the hub, a master per
# system, a detection app per running system -- and only the process being
# asked answered /api/logs. So the Logs page on a live board held two loggers
# while the same board's app.log carried
#
#     [HailoRT] [error] CHECK failed - Lost communication with the server.
#
# which is the failure the operator opened the page to find. Cameras, the
# accelerator, detection and alert sending all live in the app process. The
# one thing the page is for was the one thing it could not show.
#
# core/services/log_collect.py does the collecting and is not under test here
# -- it has its own design and its own owner. This pins the WIRING and the
# places the two halves have to agree, which is where this kind of work
# actually breaks:
#
#   * the route calls merge() at all, and publishes sources() for the filter;
#   * a collected row carries every key a local row does, so one table can
#     draw both;
#   * meta.approx_time survives, because a time shown as exact when it is a
#     file's mtime is worse than no time at all;
#   * the catalog has entries for the three cases the collector was having to
#     work around with wording that did not fit.
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import log_catalog as catalog             # noqa: E402
from core.services import log_collect                        # noqa: E402
from core.services import logs as logs_service               # noqa: E402


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the route actually calls the collector ------------------------------------
#
# Until this lands the Log Hub shows one process, which is the whole exercise.
route = read("core", "routes", "logs.py")
listing = route[route.index('name="logs.list"'):route.index('name="logs.catalog"')]
assert "log_collect.merge(" in listing, (
    "the Logs route never calls merge(), so the page still shows only the "
    "process that answered -- and the app process, where the cameras and the "
    "accelerator live, stays invisible")
assert "log_collect.sources(" in listing, \
    "sources() is not published, so the page has no source filter to offer"
# Each page is its own process (Tok Aba, 2026-10-14): the route itself drops
# any row whose source is not this process's own, after the merge, so the
# rule does not rest on the page's filter.
assert "log_collect.own_sources(request.app.state)" in listing \
    and listing.index("log_collect.merge(") < listing.index("own_sources("), \
    "the Logs route does not enforce this process's own sources server side"
assert "cookie" not in listing.split("log_collect.merge(")[1].split(")")[0], \
    "the route still forwards the cookie to a fan-out that no longer exists"
assert listing.index("logs_service.query(") < listing.index("log_collect.merge("), \
    "merge() is called before the local ring is read, so this process's own " \
    "records are not in what gets merged"
print("== the Logs route merges the other processes in and publishes the "
      "source filter")

# -- one vocabulary for the origin ---------------------------------------------
#
# He emitted `src`, the page reads `source`, and `source` itself used to hold
# "module:lineno". Two near-miss names for one value is how a blank column
# ships. `source` won: it is what the page already reads, it is the only
# unabbreviated spelling in the record, and the name was free the moment
# module/file/line became fields of their own.
collect = read("core", "services", "log_collect.py")
assert '"src"' not in collect and "'src'" not in collect, (
    "the collector still writes a `src` key while the page reads `source`:\n"
    + "\n".join(l for l in collect.splitlines() if "src\"" in l or "src'" in l))
assert '"source": src' in collect, "the collector stopped stamping the origin"
for value in ('"hub"', 'f"master:{slug}"', 'f"app:{slug}"'):
    assert value in collect, f"the collector's vocabulary lost {value}"
assert logs_service.SOURCE_HUB == "hub"
assert logs_service.source_of_master("line1") == "master:line1"
assert logs_service.source_of_app("line1") == "app:line1"
assert log_collect.app_source("line1") == logs_service.source_of_app("line1")
print("== one name, `source`, and one vocabulary for what goes in it")

# The old meaning must not come back anywhere, in either half.
for where, src in (("the record", read("core", "services", "logs.py")),
                   ("the collector", collect)):
    assert 'f"{record.module}:{record.lineno}"' not in src, (
        f"{where} is packing module:lineno back into a field -- module, file "
        f"and line are their own columns now")
print("== module:lineno is gone from both halves")

# -- a collected row draws in the same table as a local one --------------------
#
# The app's spool is written by systems/demo/app/faults.py, which cannot
# import core/ (it is spawned standalone on a bare Pi), so its rows are built
# by hand on the far side. Normalising on the way out is what stops a
# half-rendered row.
spooled = log_collect._expand(
    {"ts": 1_700_000_000.0, "level": "ERROR", "logger": "app.camera",
     "event_code": "CAM_STREAM_STALLED", "message": "no frames for 12s",
     "source": "camera:418", "meta": {"camera": "usb0"}},
    logs_service.source_of_app("line1"))
row = logs_service.as_record(spooled)
missing = [f for f in logs_service.RECORD_FIELDS if f not in row]
assert not missing, f"a collected row is missing columns: {missing}"
assert row["source"] == "app:line1", row["source"]
assert row["event_id"] == "CAM-2020", row
assert row["message"] == catalog.CATALOG["CAM_STREAM_STALLED"].message, (
    "the app's own sentence reached the page instead of the catalog's, so "
    "the same fault reads one way from the app and another from the master")
assert "no frames for 12s" in row["detail"], row["detail"]
# module/line came out of the spool's own format into their own fields.
assert row["module"] == "camera" and row["line"] == 418, row
print("== a row collected from the app carries every column a local row does")

# `seq` and `run` are stamped by whoever WROTE the record. The app does not
# stamp them yet, so they arrive empty rather than wrong -- a fabricated
# sequence number would be worse than an absent one, because the page would
# dedupe on it.
assert row["seq"] == 0 and row["run"] == "", (
    "a collected row invented a seq or a run. Those identify the writing "
    "process's own counter; making one up here silently merges records from "
    "different processes: " + repr((row["seq"], row["run"])))
print("== seq and run stay empty on a row whose writer does not stamp them")

# -- an approximate time stays marked approximate ------------------------------
#
# HailoRT prints from C++ straight to fd 2, so those lines never pass through
# Python logging and have no timestamp of their own; the collector uses the
# file's mtime and says so. A time the page presents as exact when it is not
# is worse than no time: support calls turn on when something happened.
native = logs_service.as_record({
    "ts": 1_700_000_000.0, "level": "CRITICAL", "logger": "app.hailort",
    "event_code": "AI_SERVICE_CRASHED", "message": "x", "detail": "y",
    "meta": {"approx_time": True, "repeats": 40},
    "source": logs_service.source_of_app("line1")})
assert native["meta"].get("approx_time") is True, (
    "approx_time was dropped on the way out, so the page will draw a file's "
    "mtime as the moment the accelerator died: " + repr(native["meta"]))
assert native["meta"].get("repeats") == 40, native["meta"]
# Approximate is now the FALLBACK, not the rule: the app writes a time marker
# into app.log once a minute, and a scraped line dated from the nearest marker
# above it carries a real time and says approx_time false. The flag has to
# stay conditional -- always true would waste the markers, always false would
# present a file's mtime as the moment the accelerator died.
scratch = os.path.join(tempfile.mkdtemp(), "app.log")
with open(scratch, "w", encoding="utf-8", newline="\n") as handle:
    handle.write("[HailoRT] [error] CHECK failed - Lost communication with "
                 "the server.\n")
undated = log_collect.native_records(scratch, "app:line1")
assert undated and undated[0]["meta"]["approx_time"] is True, (
    "a line with nothing above it to date from was presented as exact, so "
    "the page draws a file's mtime as the moment the accelerator died: "
    + repr(undated))
with open(scratch, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(time.strftime("[APP] ==== %Y-%m-%d %H:%M:%S ====\n"))
    handle.write("[HailoRT] [error] CHECK failed - Lost communication with "
                 "the server.\n")
marked = log_collect.native_records(scratch, "app:line1")
assert marked and marked[0]["meta"]["approx_time"] is False, (
    "a line dated from a real marker still claims to be approximate: "
    + repr(marked))
print("== an approximate timestamp stays marked as approximate")

# The normaliser must never overwrite a meta dict that has content in it.
assert logs_service.as_record({"meta": {"approx_time": True}})["meta"] == \
    {"approx_time": True}
assert logs_service.as_record({})["meta"] == {}
print("== normalising fills gaps and overwrites nothing")

# -- the catalog gaps the collector was working around -------------------------
#
# Each of these was being logged under an entry whose wording did not fit,
# which is the same failure the catalog exists to prevent, one level up.
gaps = {
    # A camera on the line, not another board. NET_PEER_UNREACHABLE says
    # "another board", which is wrong in front of an operator holding an
    # RTSP address.
    "CAM_UNREACHABLE": ("Camera", "camera"),
    # A hub or a master crashing. SYS_APP_EXITED says "the detection
    # application", which reads oddly for either.
    "SYS_PROCESS_CRASHED": ("System", "program"),
    "SYS_TASK_STOPPED": ("System", "background task"),
    # An output or alert that failed for a reason that is not the network;
    # those records shipped uncatalogued.
    "SYS_OUTPUT_FAILED": ("System", "alert or output"),
}
for code, (category, phrase) in gaps.items():
    entry = catalog.CATALOG.get(code)
    assert entry is not None, f"{code} is missing, so those records stay wrong"
    assert entry.category == category, (code, entry.category)
    assert phrase in entry.message.lower(), (code, entry.message)
    assert entry.severity in catalog.SEVERITIES

assert "another board" in catalog.CATALOG["NET_PEER_UNREACHABLE"].message.lower(), \
    "NET_PEER_UNREACHABLE was reworded to cover cameras; that is what " \
    "CAM_UNREACHABLE is for, and one entry covering two faults is one " \
    "entry that describes neither"
assert "detection application" in catalog.CATALOG["SYS_APP_EXITED"].message
print(f"== the {len(gaps)} entries the collector was working around now exist")

# -- the contract the frontend is built against is served, not written twice ---
cat_route = route[route.index('name="logs.catalog"'):]
assert "record_fields" in cat_route, (
    "the record's key list is not served, so the frontend keeps its own copy "
    "and a field that goes missing is a blank cell nobody notices")
assert "RECORD_FIELDS" in cat_route
for banned in ("metadata", "app_version", "status_code"):
    assert banned not in logs_service.RECORD_FIELDS, banned
assert "meta" in logs_service.RECORD_FIELDS
assert "version" in logs_service.RECORD_FIELDS
assert "http_status" in logs_service.RECORD_FIELDS
print("== the record's key list is served from one place, in the agreed "
      "spellings")

print("ALL OK")
