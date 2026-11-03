# -*- coding: utf-8 -*-
"""
history.py -- a time series for every live stat key, sampled on the master.

The live stats (zone counts, line tallies, fn_* function outputs) are instant
values: the sync loop overwrites them every cycle and nothing remembers what
they were an hour ago. A chart widget needs exactly that memory. This service
samples the aggregate stats every SAMPLE_S seconds into bounded per-key rings,
and appends each sample to a JSONL day-file so a restart does not blank every
chart on the wall.

In-memory bounds: 24h of 10s samples is 8,640 points per key -- small. The
5-minute rollup ring covers 7 days for the "7d" range. Day-files live under
<store>/history/YYYY-MM-DD.jsonl; only today's (and yesterday's, for the 24h
window crossing midnight) are read back at boot, and files older than 14 days
are pruned on rotation.

Called from the master's sync loop (already periodic, already off the event
loop); sample() self-rate-limits, so callers never track cadence.
"""

import datetime
import json
import math
import os
import threading
import time
from collections import deque

from core import config as config_mod

SAMPLE_S = 10
KEEP_S = 24 * 3600                  # fine ring: 24h of raw samples
ROLLUP_S = 300                      # coarse ring: 5-min averages
ROLLUP_KEEP_S = 30 * 24 * 3600     # ... kept for 30 days (time-series widgets)
MAX_KEYS = 400                     # runaway key-space guard
#: Floor for day-file retention. The EFFECTIVE retention is the admin's
#: [retention].history_months (default 12, max 12 -- see
#: core/services/retention.py); this constant only guarantees the in-memory
#: 30d window can always be rebuilt from disk. The hub's retention sweep
#: gzips files past the window and deletes past the policy; the prune here is
#: the master-side backstop and honours the same policy.
PRUNE_DAYS = 31


def _retention_days():
    """How long day-files live, from the hub's retention policy. 12 months by
    default; never below PRUNE_DAYS so charts can always rebuild."""
    try:
        raw = (config_mod.storage() or {}).get("retention") or {}
        months = int(float(raw.get("history_months", 12)))
    except (TypeError, ValueError):
        months = 12
    months = max(1, min(12, months))
    return max(PRUNE_DAYS, int(months * 30.5))

RANGES = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
          "6h": 6 * 3600, "12h": 12 * 3600, "24h": KEEP_S,
          "7d": 7 * 24 * 3600, "30d": ROLLUP_KEEP_S}


def span_of(range_key, now=None):
    """Seconds a range key covers, DAY-ALIGNED keys included.

    "today" is not a fixed span: it runs from local midnight to now -- the
    chart an operator reading "today's counts" actually means, growing
    through the shift instead of dragging in yesterday evening. Floored at
    a minute so the first moments after midnight still draw an axis.
    """
    now = time.time() if now is None else now
    if range_key == "today":
        local = time.localtime(now)
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                                0, 0, 0, 0, 0, -1))
        return max(60.0, now - midnight)
    if range_key not in RANGES:
        # The chart time axis's longer quick picks ("Last 3 hours", "Past 3
        # months"...). A window past the rollup ring's 30 days reads what the
        # ring still holds -- the samples are a ring, not an archive.
        from core.services import timeaxis                  # noqa: PLC0415
        since, until = timeaxis.window(range_key, now)
        if since is not None:
            return max(60.0, until - since)
    return RANGES.get(range_key) or KEEP_S

#: Bucket sizes a time-series widget may pin. "auto" (absent) picks from the
#: range. Anything finer than the sampler (10s) or the rollup (5m, for ranges
#: beyond 24h) is clamped -- an interval the data cannot honour would chart
#: mostly gaps.
INTERVALS = {"10s": 10, "1m": 60, "5m": 300, "15m": 900,
             "1h": 3600, "1d": 86400}

#: Per-series bucket aggregation. "last" is the classic sampler view; count is
#: "how many samples", which for event-ish keys reads as activity.
#: "unique": how many DIFFERENT values were sampled in the cell (Count
#: Difference Value), worked out from the retained samples themselves.
#: "changes": how many times the sampled value differed from the sample
#: before it (Count Value Changes), along the whole window.
AGGS = ("avg", "sum", "min", "max", "last", "count", "unique", "changes")

_lock = threading.Lock()
_fine = {}                          # key -> deque[(ts, value)]
_coarse = {}                        # key -> deque[(ts, value)]
_rollup_acc = {}                    # key -> [sum, count] within current window
_rollup_started = 0.0
_last_sample = 0.0
_loaded = False


def _dir():
    d = config_mod.store_dir() / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_path(ts):
    day = datetime.date.fromtimestamp(ts).isoformat()
    return os.path.join(str(_dir()), f"{day}.jsonl")


def _load_recent():
    """Reload today's and yesterday's day-files into the rings at first use,
    so a master restart keeps its 24h charts."""
    global _loaded, _rollup_started
    _loaded = True
    now = time.time()
    _rollup_started = now
    for offset in (86400, 0):       # yesterday first, so order stays sorted
        path = _day_path(now - offset)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        ts = float(row["ts"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if now - ts > KEEP_S:
                        continue
                    for key, value in (row.get("v") or {}).items():
                        _fine.setdefault(key, deque()).append((ts, value))
        except OSError:
            continue
    # Older day-files (up to the 30d retention) feed the COARSE ring only:
    # per-key 5-minute means, so a week/month chart survives a restart without
    # holding a month of raw samples in memory.
    try:
        names = sorted(n for n in os.listdir(str(_dir())) if n.endswith(".jsonl"))
    except OSError:
        names = []
    today = datetime.date.fromtimestamp(now)
    for name in names:
        try:
            day = datetime.date.fromisoformat(name[:-6])
        except ValueError:
            continue
        age_days = (today - day).days
        if age_days <= 1 or age_days * 86400 > ROLLUP_KEEP_S:
            continue                # recent days come from the fine ring below
        acc = {}                    # key -> {bucket_ts: [sum, count]}
        try:
            with open(os.path.join(str(_dir()), name), "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        ts = float(row["ts"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    b = int(ts // ROLLUP_S) * ROLLUP_S
                    for key, value in (row.get("v") or {}).items():
                        cell = acc.setdefault(key, {}).setdefault(b, [0.0, 0])
                        cell[0] += value
                        cell[1] += 1
        except OSError:
            continue
        for key, buckets_ in acc.items():
            ring = _coarse.setdefault(key, deque())
            for b in sorted(buckets_):
                total, count = buckets_[b]
                ring.append((float(b), round(total / count, 4)))
    # Rebuild the recent coarse tail from the fine ring -- approximate (raw
    # points, not true 5-min means) but enough that charts are not empty at boot.
    for key, ring in _fine.items():
        coarse = _coarse.setdefault(key, deque())
        last = coarse[-1][0] if coarse else 0.0
        for ts, value in ring:
            if ts - last >= ROLLUP_S:
                coarse.append((ts, value))
                last = ts


def sample(stats, now=None):
    """Record one snapshot of the numeric aggregate stats. Rate-limited to one
    sample per SAMPLE_S; extra calls are free no-ops, so the sync loop just
    calls it every cycle."""
    global _last_sample, _rollup_started
    now = time.time() if now is None else now
    with _lock:
        if not _loaded:
            _load_recent()
        if now - _last_sample < SAMPLE_S:
            return
        _last_sample = now

        numeric = {}
        for key, value in (stats or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if len(_fine) >= MAX_KEYS and key not in _fine:
                continue
            numeric[key] = value
            ring = _fine.setdefault(key, deque())
            ring.append((now, value))
            while ring and now - ring[0][0] > KEEP_S:
                ring.popleft()
            acc = _rollup_acc.setdefault(key, [0.0, 0])
            acc[0] += value
            acc[1] += 1

        # Close the 5-minute rollup window.
        if now - _rollup_started >= ROLLUP_S and _rollup_acc:
            for key, (total, count) in _rollup_acc.items():
                if not count:
                    continue
                ring = _coarse.setdefault(key, deque())
                ring.append((now, round(total / count, 4)))
                while ring and now - ring[0][0] > ROLLUP_KEEP_S:
                    ring.popleft()
            _rollup_acc.clear()
            _rollup_started = now
            _prune_files(now)

        if numeric:
            try:
                with open(_day_path(now), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": round(now, 1), "v": numeric}) + "\n")
            except OSError:
                pass                # a full disk costs persistence, not charts


def _prune_files(now):
    keep_days = _retention_days()
    try:
        for name in os.listdir(str(_dir())):
            if not name.endswith(".jsonl"):
                continue
            try:
                day = datetime.date.fromisoformat(name[:-6])
            except ValueError:
                continue
            if (datetime.date.fromtimestamp(now) - day).days > keep_days:
                try:
                    os.remove(os.path.join(str(_dir()), name))
                except OSError:
                    pass
    except OSError:
        pass


def keys():
    with _lock:
        return sorted(_fine)


def grid(range_key="24h", interval_key="", now=None, since=None):
    """The bucket axis a time chart is drawn on: {start, step, buckets, labels,
    coarse, span}.

    Split out of series_agg because the sampler is no longer the only thing
    that draws on this axis. A chart may now mix a sampled stat with an event
    query and a dataset column, and those have to land on the SAME buckets or
    the picture is three charts overlaid -- so the grid is derived once, here,
    and every source is resampled onto it (see services/chart_series.py).

    The step is never finer than the ring feeding it (10s raw, 5m rollup):
    an interval the sampler cannot honour charts mostly gaps. That floor is
    also what keeps a mixed chart honest -- a 10-second grid against an
    hourly event bucket would draw one event point per hour and 359 holes,
    so the coarsest MANDATORY resolution wins and the finer source is folded
    into it rather than interleaved.
    """
    now = time.time() if now is None else now
    # `since` is an explicit window start: the Analytics page's own date pick.
    # `now` is then the window's END, so a chart can cover last Tuesday from
    # the rollup ring rather than only a span ending this second.
    from core.services import timeaxis                      # noqa: PLC0415
    span = max(60.0, now - since) if since else span_of(range_key, now)
    coarse = span > KEEP_S
    floor_step = ROLLUP_S if coarse else SAMPLE_S
    # A picked step ("Group by time": every 30 minutes, every 6 hours, a
    # custom 90 minutes -- services/timeaxis.py) is the cell size; widened
    # when the range would hold more than the grid's 1000 cells, and said.
    picked = timeaxis.clean_step(interval_key) or ""
    widened = ""
    if picked:
        fitted, grew = timeaxis.fit(picked, span, limit=1000)
        widened = fitted if grew else ""
        step = max(timeaxis.step_seconds(fitted), floor_step)
        # On the step's own edges (every hour at :00, every 6 hours at
        # 00/06/12/18 local) rather than counted back from this second.
        edge_key = fitted if timeaxis.step_seconds(fitted) == step else "%ds" % step
        start = timeaxis.floor(now - span, edge_key)
        buckets = max(1, min(1000, int(math.ceil((now - start) / step))))
    else:
        step = max(floor_step, span // 120)
        buckets = max(1, min(1000, int(span // step)))
        start = now - buckets * step
    fmt = "%H:%M:%S" if span <= 1800 else "%H:%M" if span <= KEEP_S else "%d %b %H:%M"
    labels = [datetime.datetime.fromtimestamp(start + b * step).strftime(fmt)
              for b in range(buckets)]
    return {"start": start, "step": step, "buckets": buckets, "labels": labels,
            "coarse": coarse, "span": span, "widened": widened,
            "keys": [start + b * step for b in range(buckets)]}


def has(key):
    """Whether the sampler has ever seen this key. A chart series naming a key
    that does not exist must say so (an empty series and a warning); without
    this it is indistinguishable from a key that exists and read null all
    window, which is a chart quietly lying about a typo."""
    with _lock:
        if not _loaded:
            _load_recent()
        return bool(key) and (key in _fine or key in _coarse)


def values_on_grid(key, agg, g):
    """One key folded onto an EXPLICIT grid (see grid()), as a plain list.

    Empty buckets are None -- except count/sum, where nothing sampled really
    is zero of them. That distinction is the whole reason this returns None
    at all: a zero draws a line to the floor and claims something was
    measured there.
    """
    start, step, buckets = g["start"], g["step"], g["buckets"]
    end = start + buckets * step
    agg = agg if agg in AGGS else "avg"
    with _lock:
        if not _loaded:
            _load_recent()
        source = _coarse if g.get("coarse") else _fine
        cells = [[] for _ in range(buckets)]
        if agg == "changes":
            # Along the whole window, sample by sample: a change at a cell
            # boundary counts in the cell where the new value appears (so
            # cells add up exactly); the first sample in the window counts.
            counts, prev = [0] * buckets, None
            for ts, value in (source.get(str(key or "")) or ()):
                if ts < start or ts >= end or value is None:
                    continue
                if prev is None or value != prev:
                    counts[min(buckets - 1, int((ts - start) // step))] += 1
                prev = value
            return counts
        for ts, value in (source.get(str(key or "")) or ()):
            # BOTH bounds. Clamping late samples into the last bucket was
            # right for a window ending now; for a PICKED window ending last
            # Tuesday it dragged the rest of the week into the final point.
            if ts < start or ts >= end:
                continue
            cells[min(buckets - 1, int((ts - start) // step))].append(value)
    values = []
    for cell in cells:
        if not cell:
            values.append(0 if agg in ("count", "sum", "unique") else None)
        elif agg == "avg":
            values.append(round(sum(cell) / len(cell), 4))
        elif agg == "sum":
            values.append(round(sum(cell), 4))
        elif agg == "min":
            values.append(min(cell))
        elif agg == "max":
            values.append(max(cell))
        elif agg == "count":
            values.append(len(cell))
        elif agg == "unique":
            values.append(len(set(cell)))
        else:                                               # last
            values.append(cell[-1])
    return values


def trim_leading(labels, datasets):
    """Drop the leading buckets NO series covers -- a mostly-empty left pad
    reads as a broken chart. Shared with the multi-source resolver so a mixed
    chart trims by the same rule a sampler-only one does."""
    for first in range(len(labels)):
        if any(d["values"][first] not in (None, 0) for d in datasets):
            break
    else:
        first = 0
    return (labels[first:],
            [{**d, "values": d["values"][first:]} for d in datasets])


def series_agg(specs, range_key="24h", interval_key="", now=None, since=None):
    """Multi-series time series with per-series aggregation, for the Builder's
    time-series widgets (line/area/spline).

    `specs` is [{key, agg, label}] -- every sample in a bucket is folded by
    that series' aggregation (avg/sum/min/max/last/count). `interval_key`
    pins the bucket size; absent/auto derives it from the range (~120 buckets).
    Ranges beyond 24h read the 5-minute rollup ring; a pinned interval finer
    than the source data is clamped up rather than charting gaps.

    Returns {labels, datasets: [{key, label, agg, values}], range, interval_s}.
    """
    g = grid(range_key, interval_key, now=now, since=since)
    datasets = []
    for spec in specs or ():
        key = str(spec.get("key") or "")
        agg = spec.get("agg") if spec.get("agg") in AGGS else "avg"
        datasets.append({"key": key, "agg": agg,
                         "label": str(spec.get("label") or key),
                         "color": str(spec.get("color") or ""),
                         "values": values_on_grid(key, agg, g)})
    labels, datasets = trim_leading(g["labels"], datasets)
    return {"labels": labels, "datasets": datasets,
            "range": range_key, "interval_s": g["step"]}


def series(wanted, range_key="24h", now=None, since=None):
    """Aligned time series for the given keys: {labels: [...], series: {key:
    [...]}}. The 7d range reads the 5-min rollups; 1h/24h read raw samples.
    Points are bucketed onto a common label axis so several keys chart
    together; a key with no sample in a bucket carries None (chart gap).
    `since` + `now` bound an explicit window (see series_agg)."""
    now = time.time() if now is None else now
    span = max(60.0, now - since) if since else span_of(range_key, now)
    coarse = span > KEEP_S
    step = ROLLUP_S if coarse else max(SAMPLE_S, span // 360)   # <= ~360 points
    start = now - span

    with _lock:
        if not _loaded:
            _load_recent()
        source = _coarse if coarse else _fine
        buckets = int(span // step)
        labels = []
        fmt = "%H:%M" if span <= KEEP_S else "%d %b %H:%M"
        for b in range(buckets):
            labels.append(datetime.datetime.fromtimestamp(
                start + b * step).strftime(fmt))
        out = {}
        for key in wanted or ():
            ring = source.get(key) or ()
            values = [None] * buckets
            end = start + buckets * step
            for ts, value in ring:
                # Both bounds -- same reason as series_agg: a picked window
                # ending in the past must not fold later samples into its
                # final point.
                if ts < start or ts >= end:
                    continue
                b = min(buckets - 1, int((ts - start) // step))
                values[b] = value
            out[key] = values
        # Trim leading buckets where NO key has data -- a chart that is 90%
        # empty left-pad reads as broken.
        first = 0
        for i in range(buckets):
            if any(v[i] is not None for v in out.values()):
                first = i
                break
        return {"labels": labels[first:],
                "series": {k: v[first:] for k, v in out.items()},
                "range": range_key}
