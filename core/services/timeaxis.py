"""The time axis of a chart: how far apart its points are (the STEP, "Group
by time") and which stretch of time it covers (the RANGE, "Time range").

WHY ONE MODULE. Before this, a chart's time axis was decided in five places
with five vocabularies: the node's "Time axis" (hour/day/week/month), a
history chart's "Interval" (10s..1d), an event card's "Time bucket" (10s..
month), the history grid's own step (span // 120) and the store's
"<n>s"/"<n>m" grammar below an hour. The owner asked for one model -- every
1, 30 or 60 minutes, every 1, 6, 12 or 24 hours, daily, and custom -- and a
range picked in the same plain words everywhere, filtered in the database
query itself (WHERE ts >= from AND ts < to) rather than trimmed afterwards.

THE STORED VALUES (backwards compatible -- nothing already saved changes
meaning):

  step   ""                 automatic: a step that fits the range (as before)
         "<n>m" "<n>h" "<n>d"  every n minutes / hours / days, n a whole
                            number, 1 minute .. 90 days
         "week" "month"     calendar weeks (Monday) and months
         legacy, still read: "hour" (= "1h"), "day" (= "1d"), "10s", "15m"
  range  "3h" "6h" "12h" "24h" "7d" "30d" "90d" "180d" "365d"  rolling,
                            ending now
         "today" "week" "month"  day-aligned, from local midnight today /
                            6 days ago / 29 days ago (the old Period words,
                            meaning exactly what builder._range_floor always
                            made of them)
         "page"             follow the Analytics date picker
         "custom"           exact start and end, in range_from / range_to
                            (ISO 8601, local time when no offset is given)
         legacy, still read: "5m" "15m" "30m" "1h" (history), "" (all time)

THE BUCKETS. Every step lands on the same edges in every store: calendar
steps by date_trunc in the session's zone; whole-hour and whole-day steps
on LOCAL midnight (epoch arithmetic shifted by the box's UTC offset), so
"every 6 hours" means 00:00, 06:00, 12:00, 18:00 on the box's clock and a
day bucket is a local day -- the same day key the store has always used.
Steps below an hour floor on the epoch exactly as before.

THE LIMIT. A range divided by a step can be absurd (a year every minute is
half a million points). Past MAX_POINTS the step is widened to the nearest
preset that fits, and the chart says so -- never a timeout, never a huge
payload.
"""

import datetime
import re
import time

#: The most points one chart's time axis may hold. Matches the store's own
#: zero-fill ceiling (events_repo.MAX_FILLED_BUCKETS).
MAX_POINTS = 2000

#: Custom step bounds: a minute (finer than any store keeps per row anyway)
#: to 90 days (past that, "every month" is the honest question).
MIN_STEP_S = 60
MAX_STEP_S = 90 * 86400

#: The steps the pickers offer, in the order they read, as (value, words).
STEP_PRESETS = [
    ("", "Automatic"), ("1m", "Every minute"), ("5m", "Every 5 minutes"),
    ("30m", "Every 30 minutes"), ("1h", "Every hour"), ("6h", "Every 6 hours"),
    ("12h", "Every 12 hours"), ("1d", "Every day"), ("week", "Every week"),
    ("month", "Every month"),
]

#: Old step names, read as their new ones.
STEP_ALIASES = {"hour": "1h", "day": "1d", "1w": "week"}

_STEP_RE = re.compile(r"^(\d{1,5})([smhd])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: The rolling ranges, in seconds.
ROLLING = {"3h": 3 * 3600, "6h": 6 * 3600, "12h": 12 * 3600, "24h": 86400,
           "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400,
           "180d": 180 * 86400, "365d": 365 * 86400,
           # history's short windows, still stored on older charts
           "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
#: The quick picks, in the order they read, as (value, words).
RANGE_PRESETS = [
    ("3h", "Last 3 hours"), ("6h", "Last 6 hours"), ("12h", "Last 12 hours"),
    ("24h", "Last 24 hours"), ("today", "Today"),
    ("7d", "Past week"), ("30d", "Past month"),
    ("90d", "Past 3 months"), ("180d", "Past 6 months"), ("365d", "Past year"),
    ("page", "Follow the Analytics date picker"), ("custom", "Pick dates..."),
]
CALENDAR = ("today", "week", "month")
RANGE_KEYS = tuple(ROLLING) + CALENDAR + ("page", "custom")


# -- step ---------------------------------------------------------------------

def clean_step(value):
    """A step as it may be stored, or None when it cannot be (junk, zero,
    negative, below a minute, beyond 90 days). "" (automatic) is a step."""
    s = str(value if value is not None else "").strip().lower()
    s = STEP_ALIASES.get(s, s)
    if s in ("", "week", "month"):
        return s
    m = _STEP_RE.match(s)
    if not m:
        return None
    n = int(m.group(1))
    secs = n * _UNIT_S[m.group(2)]
    # "10s" is the sampler's own resolution and was a stored history
    # interval; every other step is a minute or more.
    if m.group(2) == "s":
        return s if secs in (10,) or (60 <= secs < 3600 and secs % 60 == 0) else None
    if n < 1 or secs < MIN_STEP_S or secs > MAX_STEP_S:
        return None
    return s


def step_seconds(step):
    """Seconds one step spans: a calendar week is 7 days, a month is taken
    as 30 days for sizing only (its edges are the real month edges)."""
    s = clean_step(step)
    if not s:
        return None
    if s == "week":
        return 7 * 86400
    if s == "month":
        return 30 * 86400
    m = _STEP_RE.match(s)
    return int(m.group(1)) * _UNIT_S[m.group(2)]


def step_words(step):
    """'Every 6 hours', 'Every 90 minutes', ... for hints and axis titles."""
    s = clean_step(step)
    for value, words in STEP_PRESETS:
        if value == s:
            return words
    if not s:
        return ""
    m = _STEP_RE.match(s)
    n, unit = int(m.group(1)), {"s": "second", "m": "minute",
                                "h": "hour", "d": "day"}[m.group(2)]
    return f"Every {n} {unit}{'' if n == 1 else 's'}"


#: Automatic and widened steps are picked from these, finest first.
_LADDER = ["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d", "week", "month"]


def auto_step(span_s, target=120):
    """The step 'Automatic' means for a span: the finest preset that keeps the
    axis to about `target` points."""
    for step in _LADDER:
        if span_s / step_seconds(step) <= target:
            return step
    return "month"


def fit(step, span_s, limit=MAX_POINTS):
    """(step, widened): the step to draw with, widened to the nearest ladder
    preset when the range holds more than `limit` of them."""
    s = clean_step(step)
    if not s:
        return auto_step(span_s), False
    if span_s / step_seconds(s) <= limit:
        return s, False
    for candidate in _LADDER:
        if step_seconds(candidate) > step_seconds(s) \
                and span_s / step_seconds(candidate) <= limit:
            return candidate, True
    return "month", True


def store_bucket(step):
    """The bucket key the stores understand for a step (events_repo grammar):
    the calendar names where date_trunc is exact, "<n>s" otherwise."""
    s = clean_step(step)
    if s in ("week", "month"):
        return s
    if s == "1h":
        return "hour"
    if s == "1d":
        return "day"
    secs = step_seconds(s)
    return f"{secs}s" if secs else None


# -- buckets -------------------------------------------------------------------

def local_offset(epoch=None):
    """The box's UTC offset in seconds at `epoch` (DST-aware)."""
    t = time.localtime(time.time() if epoch is None else epoch)
    return int(t.tm_gmtoff or 0)


def floor(epoch, step):
    """The start of the bucket `epoch` falls in, as an epoch."""
    s = clean_step(step)
    dt = datetime.datetime.fromtimestamp(epoch).astimezone()
    if s == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    if s == "week":
        day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return (day - datetime.timedelta(days=day.weekday())).timestamp()
    secs = step_seconds(s)
    if not secs:
        return epoch
    if secs < 3600:
        return epoch // secs * secs
    # Whole hours and days: on LOCAL midnight, so "every 6 hours" starts at
    # 00:00 on the box's clock and a day is a local day.
    off = local_offset(epoch)
    return (epoch + off) // secs * secs - off


def next_edge(edge, step):
    s = clean_step(step)
    if s == "month":
        dt = datetime.datetime.fromtimestamp(edge).astimezone()
        y, m = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
        return dt.replace(year=y, month=m).timestamp()
    if s == "week":
        return floor(edge + 7 * 86400 + 3 * 3600, "week")
    secs = step_seconds(s)
    if secs >= 86400:
        # A local day can be 23 or 25 hours long across a clock change; step
        # to the next local midnight rather than adding 86400.
        return floor(edge + secs + 3 * 3600, s)
    return edge + secs


def axis(since, until, step, limit=MAX_POINTS):
    """Every bucket start from the one holding `since` to the last before
    `until`, as epochs -- the x axis, so a period nobody recorded anything in
    is still on it."""
    out = []
    edge = floor(since, step)
    while edge < until and len(out) < limit:
        out.append(edge)
        edge = next_edge(edge, step)
    return out


# -- range ---------------------------------------------------------------------

def clean_range(value):
    s = str(value or "").strip()
    return s if s in RANGE_KEYS or s == "" else None


def parse_moment(value):
    """An ISO date or datetime as an epoch; a date or a naive time is LOCAL."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def clean_moment(value):
    """A custom range end as it may be stored: ISO text, or "" when unset."""
    s = str(value or "").strip()[:32]
    return s if s and parse_moment(s) is not None else ""


def window(range_key, now=None, range_from="", range_to=""):
    """(since, until) epochs a range covers, or (None, None) for all time.
    "page" is resolved by the caller (builder.compute) before this is asked.
    A custom date with no time runs to the END of that day."""
    now = time.time() if now is None else now
    r = clean_range(range_key)
    if r in ROLLING:
        return now - ROLLING[r], now
    if r in CALENDAR:
        days = {"today": 1, "week": 7, "month": 30}[r]
        midnight = floor(now, "1d")
        return floor(midnight - (days - 1) * 86400 + 3 * 3600, "1d"), now
    if r == "custom":
        lo = parse_moment(range_from)
        hi = parse_moment(range_to)
        if hi is not None and len(str(range_to).strip()) == 10:
            hi += 86400                    # a bare end date includes that day
        if lo is None:
            return None, None
        # An open or future end charts up to NOW: the hours not yet lived
        # have nothing to show.
        hi = now if hi is None or hi > now else hi
        return (lo, max(hi, lo + 60))
    return None, None


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat()
