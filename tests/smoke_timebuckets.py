# Time, asked three ways, answered consistently.
#
# A card over recorded data mixes three windows: which ROWS count (the
# period), how they are GROUPED (the bucket), and where the window's edges
# fall. Three defects lived in that seam:
#
#   * the database counted `ts <= until` while the in-memory fallback counted
#     `ts < until` -- an event stamped exactly at midnight belonged to the
#     prior day or the next one depending on whether Postgres was up;
#   * a GROUP BY returns only buckets that contain rows, so a quiet hour
#     vanished from "detections per hour" and the chart drew across the gap;
#   * the finest bucket was an hour, and hour/day were the only choices the
#     Event Card offered while the store understood week and month too.
import datetime
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                # noqa: E402
from core.repos import events as events_repo                    # noqa: E402
from core.services import builder as builder_service            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- half-open windows everywhere ---------------------------------------------
# start <= ts < end. The boundary row counts once, in the window that starts
# there -- in the database, in the fallback, in both stores.
where, params = events_repo._where("s", {}, "2026-01-01", "2026-01-02")
check("events: the end of a window is exclusive",
      "ts < %s" in where and "ts <= %s" not in where, where)
check("...and the start inclusive", "ts >= %s" in where, where)
where, params = datasets_repo._where("s", "d", {"v": "number"}, {},
                                     "2026-01-01", "2026-01-02")
check("datasets: same contract", "ts < %s" in where and "ts <= %s" not in where,
      where)
src = read("core", "services", "builder.py")
check("the in-memory fallback already read it that way",
      '>= until' in src)

# -- the interval grammar ------------------------------------------------------
check("named calendar buckets still resolve",
      events_repo.time_bucket_exprs("hour")[0] == "date_trunc('hour', ts)")
for spec_, secs in (("10s", 10), ("1m", 60), ("5m", 300), ("15m", 900)):
    check(f"{spec_} is {secs} seconds",
          events_repo.bucket_seconds(spec_) == secs)
expr, step = events_repo.time_bucket_exprs("5m")
check("a custom interval floors the epoch, not the calendar",
      expr == "to_timestamp(floor(extract(epoch FROM ts) / 300) * 300)"
      and step == "300 seconds", repr((expr, step)))
# The bounds: finer than the sampler is noise. An hour and up is accepted
# since the chart time axis grew custom steps (services/timeaxis.py) -- "60m"
# is an hour, "6h" six of them -- floored on LOCAL midnight; past 90 days
# "every month" is the honest question.
check("bounded below and above",
      events_repo.bucket_seconds("1s") is None
      and events_repo.bucket_seconds("60m") == 3600
      and events_repo.bucket_seconds("6h") == 21600
      and events_repo.bucket_seconds("91d") is None
      and events_repo.bucket_seconds("banana") is None)
# The interval is regex-extracted digits, never the raw string -- pinned
# because these values are placed into SQL.
check("nothing but digits reaches the SQL expression",
      re.search(r"/ 300\) \* 300", expr) is not None)

# -- zero-filled series --------------------------------------------------------
inner = "SELECT date_trunc('hour', ts) AS key, count(*) AS value FROM events"
sql = events_repo.filled_series_sql(inner, "hour", "1 hour")
check("an empty bucket is a zero, not a gap",
      "generate_series" in sql and "coalesce(a.value, 0)" in sql, sql)
check("...with both ends truncated onto the bucket grid",
      sql.count("date_trunc('hour', %s::timestamptz)") == 2, sql)
# The series' two placeholders come FIRST in the wrapped statement, and
# placeholders bind in text order -- appending the window params instead of
# prepending fed "pcb_sensata" to a timestamptz cast on the live box.
for repo in ("events.py", "datasets.py"):
    check(f"{repo} binds the window params before the inner query's",
          "params = [since, fill_until] + params" in read("core", "repos", repo))
sql = events_repo.filled_series_sql(inner, "5m", "300 seconds")
check("...custom intervals step their own width",
      "interval '300 seconds'" in sql
      and "floor(extract(epoch FROM %s::timestamptz) / 300)" in sql, sql)
# A window too wide to fill is left sparse rather than clamped or exploded.
check("a sane window fills",
      events_repo._fill_span_ok("hour", "2026-01-01T00:00:00+08:00",
                                "2026-01-08T00:00:00+08:00"))
check("an absurd one does not (a year of 10s buckets)",
      not events_repo._fill_span_ok("10s", "2025-01-01T00:00:00+08:00",
                                    "2026-01-01T00:00:00+08:00"))
check("...and a backwards one does not",
      not events_repo._fill_span_ok("hour", "2026-01-02T00:00:00+08:00",
                                    "2026-01-01T00:00:00+08:00"))

# -- the fallback buckets agree with the database ------------------------------
key = builder_service._bucket_key("2026-01-01T10:07:42+08:00", "5m")
# Compare the INSTANT, not the spelling. _bucket_key floors on the epoch and
# then renders in the local zone, so the same bucket reads 10:05:00+08:00 on a
# board in Malaysia and 02:05:00+00:00 on a CI runner set to UTC. Both are the
# same moment and both group identically; asserting the +08:00 spelling pinned
# the developer's own timezone and failed everywhere else -- which is exactly
# how it blocked every release build from 2026-09-10 onwards.
WANT = datetime.datetime.fromisoformat("2026-01-01T10:05:00+08:00")
check("the in-memory fallback floors custom intervals the same way",
      datetime.datetime.fromisoformat(key) == WANT, key)
check("...and floors DOWN onto the grid, never to the nearest",
      datetime.datetime.fromisoformat(
          builder_service._bucket_key("2026-01-01T10:09:59+08:00", "5m")) == WANT)

# -- one vocabulary, offered everywhere ---------------------------------------
check("the Builder's bucket list carries the presets",
      builder_service.BUCKETS == ("10s", "1m", "5m", "15m",
                                  "hour", "day", "week", "month"),
      repr(builder_service.BUCKETS))
page = read("frontend", "src", "pages", "Workflow.jsx")
# The option lists live in the shared card fields (cardFields.jsx), drawn by
# the node's inspector and the Builders' panel alike.
fields = read("frontend", "src", "components", "widgets", "cardFields.jsx")
# Since the chart time axis model (services/timeaxis.py) the Event Card's
# bucket is the shared "Group by time" (StepField): the owner's presets, and
# any other step ("15m", "10s" already stored) as Custom or as itself.
buckets = fields.split("export const STEP_PRESETS")[1].split("];")[0]
for v in ("1m", "5m", "30m", "1h", "week", "month"):
    check(f"the Event Card offers {v}", f'["{v}",' in buckets
          and "<StepField field={Field} value={d.bucket" in page)
from core.services import timeaxis                             # noqa: E402
check("...and still reads the older 10s and 15m",
      timeaxis.clean_step("10s") == "10s" and timeaxis.clean_step("15m") == "15m")
# "Follow the Analytics range" on every Period picker: the backend resolved
# query range "page" all along (builder.compute) -- the Event Card and the
# table card simply never offered it.
periods = fields.split("export const PERIODS")[1].split("];")[0]
check("every Period picker offers the Analytics range",
      '["page", "follow the Analytics range"]' in periods
      # A card's periods are its ONE Date range since 2026-10-13
      # (CardDateRange, which offers it too); the recorded panels keep two.
      and page.count("<PeriodField") >= 2 and "<CardDateRange" in page
      and '["page", "Follow the Analytics date picker"]' in fields,
      f"found {page.count('<PeriodField')}")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
