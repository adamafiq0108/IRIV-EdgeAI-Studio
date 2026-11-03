# Chart time ranges: the full menu, a day-aligned "today", and "follow the
# Analytics range".
#
# The pickers offered 1h/24h/7d out of the nine spans the sampler already
# kept, and nothing could say "today FROM MIDNIGHT" -- a rolling 24h drags in
# yesterday evening, which is not what an operator reading "today's counts"
# means. "page" goes further: the chart follows whatever period the Analytics
# date picker holds, resolved at render so the stored widget never freezes a
# choice.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder, history                     # noqa: E402

# 1. every span the sampler keeps is offered, plus the two day-aligned keys
assert set(history.RANGES) <= set(builder.HISTORY_RANGES), \
    set(history.RANGES) - set(builder.HISTORY_RANGES)
assert "today" in builder.HISTORY_RANGES and "page" in builder.HISTORY_RANGES
print("== the range menu covers every kept span, today, and follow-the-page")

# 2. "today" spans midnight -> now, and grows through the day
noon = time.mktime((2026, 8, 13, 12, 0, 0, 0, 0, -1))
assert abs(history.span_of("today", noon) - 12 * 3600) < 2
evening = time.mktime((2026, 8, 13, 18, 30, 0, 0, 0, -1))
assert abs(history.span_of("today", evening) - 18.5 * 3600) < 2
# ...and just past midnight it still draws an axis instead of a zero span
past12 = time.mktime((2026, 8, 13, 0, 0, 10, 0, 0, -1))
assert history.span_of("today", past12) >= 60
assert history.span_of("6h", noon) == 6 * 3600
print("== 'today' runs from 12 am to now; fixed spans are unchanged")

# 3. a widget saved with the new keys survives the validator
w = builder.validate([{"id": "g1", "title": "Total", "kind": "line",
                       "source": "history", "key": "fn_x_count",
                       "range": "today"},
                      {"id": "g2", "title": "Total", "kind": "line",
                       "source": "history", "key": "fn_x_count",
                       "range": "page"}])
assert w[0]["range"] == "today" and w[1]["range"] == "page", w
print("== the validator keeps 'today' and 'page'")

# 4. "page" resolves against the Analytics picker at render time
for picked, expect in (("today", "today"), ("week", "7d"), ("month", "30d"),
                       ("", "today"), ("custom", "24h")):
    out = builder.compute([{"id": "g2", "title": "T", "kind": "line",
                            "source": "history", "key": "fn_x_count",
                            "range": "page", "span": 1}],
                          [], {}, page_range=picked)
    got = out[0]["data"]["range"] if out[0]["data"] else None
    assert got == expect, (picked, got)
print("== 'page' follows today/week/month, and falls back sanely")

# 4b. ...and the Analytics page's REAL shape: always custom + explicit dates
#     (its Today/Week/Month buttons are presets inside the date picker).
#     The window must be those days, end clamped to now.
out = builder.compute([{"id": "g2", "title": "T", "kind": "line",
                        "source": "history", "key": "fn_x_count",
                        "range": "page", "span": 1}],
                      [], {}, page_range="custom",
                      page_start="2026-08-12", page_end="2026-08-13")
assert out[0]["data"]["range"] == "custom", out[0]["data"]
want_since = time.mktime((2026, 8, 12, 0, 0, 0, 0, 0, -1))
# The injected window is what _data hands the sampler; recompute it the same
# way to pin the boundary maths.
assert builder._day_epoch("2026-08-12") == want_since
assert builder._day_epoch("2026-08-13", end=True) == want_since + 2 * 86400
assert builder._day_epoch("garbage") is None
print("== a custom date pick becomes a midnight-to-midnight window")

# 5. the series itself honours the day alignment: a sample from before
#    midnight stays out of a "today" chart
key = "fn_range_test"
midnight = time.mktime((2026, 8, 13, 0, 0, 0, 0, 0, -1))
with history._lock:
    history._loaded = True
    history._fine[key] = __import__("collections").deque(
        [(midnight - 3600, 99.0), (midnight + 3600, 5.0), (noon - 60, 7.0)])
got = history.series_agg([{"key": key, "agg": "max"}], "today", now=noon)
values = [v for v in got["datasets"][0]["values"] if v is not None]
assert 99.0 not in values and 7.0 in values, values
print("== yesterday evening stays out of a 'today' chart:", values[-3:])

# 6. an explicit window (the date pick) bounds the chart on BOTH ends: only
#    the sample inside 13 Aug 00:00-06:00 shows.
got = history.series_agg([{"key": key, "agg": "max"}], "custom",
                         now=midnight + 6 * 3600, since=midnight)
values = [v for v in got["datasets"][0]["values"] if v is not None]
assert values == [5.0], values
print("== a picked window charts exactly those hours:", values)

# 7. the DATABASE-backed twin: a counting card (dataset/query source) whose
#    QUERY says "page" follows the same date pick. Checked through the
#    no-Postgres event path, which shares the resolution.
EVENTS = [{"ts": "2026-08-11T10:00:00", "type": "info", "severity": "info",
           "label": "x", "detail": {}},
          {"ts": "2026-08-12T10:00:00", "type": "info", "severity": "info",
           "label": "x", "detail": {}},
          {"ts": "2026-08-13T10:00:00", "type": "info", "severity": "info",
           "label": "x", "detail": {}}]
tile = {"id": "t1", "title": "Total", "kind": "tile", "source": "query",
        "key": "", "span": 1, "query": {"agg": "count", "range": "page"}}
out = builder.compute([tile], EVENTS, {}, page_range="custom",
                      page_start="2026-08-12", page_end="2026-08-12")
assert out[0]["data"]["value"] == 1, out[0]["data"]
# ...and the whole three days when the pick covers them
out = builder.compute([tile], EVENTS, {}, page_range="custom",
                      page_start="2026-08-11", page_end="2026-08-13")
assert out[0]["data"]["value"] == 3, out[0]["data"]
# stored spec keeps "page" through the validator
kept = builder.validate([tile])[0]
assert kept["query"]["range"] == "page", kept["query"]
print("== a counting card follows the picked days: 1 of 3 events on 12 Aug")
print("ALL OK")
