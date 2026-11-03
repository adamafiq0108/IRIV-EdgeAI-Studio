# A chart's time axis: "Group by time" (the step) and "Time range" (the
# window), one model for every chart (services/timeaxis.py).
#
# Owner (2026-10-13): "introduce a configurable TIME INTERVAL so users choose
# how data is grouped and displayed: every 1, 30 or 60 minutes; every 1, 6,
# 12 or 24 hours; daily; and custom ... use the datetime stored in the
# database to filter and retrieve data for the exact date or date range".
#
# Pinned here, through the real compile and the real render with a fake
# store that answers the way Postgres does (rows in [since, until), floored
# onto the bucket it is asked for):
#   1. the step grammar: presets, custom 90 minutes / 2 days, and what is
#      refused (0, negative, fractions, junk, beyond 90 days)
#   2. the window: rolling picks, an exact start/end, a bare end date
#      covering its whole day, a future end charting up to now
#   3. bucket edges: every 6 hours at 00/06/12/18 local, days at local
#      midnight, months on the 1st
#   4. every step on a two-input chart, compiled from a workflow node: the
#      store is asked for that bucket over that window, both inputs share
#      one axis covering the whole window, empty steps are zero for a count
#      and gaps for an average
#   5. too many points: a year every minute is widened and the chart says so
#   6. a one-input chart takes the same path
#   7. the step and range travel from a Builder edit to the node (custom
#      values validated)
import datetime
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                  # noqa: E402
from core.services import builder as builder_service             # noqa: E402
from core.services import timeaxis as ta                         # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the step grammar --------------------------------------------------------
for good, want in (("1m", "1m"), ("30m", "30m"), ("1h", "1h"), ("6h", "6h"),
                   ("12h", "12h"), ("1d", "1d"), ("90m", "90m"), ("2d", "2d"),
                   ("week", "week"), ("month", "month"), ("", ""),
                   ("hour", "1h"), ("day", "1d"), ("10s", "10s"), ("15m", "15m")):
    check(f"step {good!r} is stored as {want!r}", ta.clean_step(good) == want,
          ta.clean_step(good))
for junk in ("0m", "-5m", "1.5h", "abc", "91d", "30s", "1000000d", "5", "m"):
    check(f"step {junk!r} is refused", ta.clean_step(junk) is None, ta.clean_step(junk))
check("custom steps are worded for the hint and the axis title",
      ta.step_words("90m") == "Every 90 minutes" and ta.step_words("2d") == "Every 2 days"
      and ta.step_words("6h") == "Every 6 hours" and ta.step_words("") == "Automatic")

# -- 2. the window -----------------------------------------------------------------
NOW = time.time()
lo, hi = ta.window("6h", NOW)
check("'Last 6 hours' ends now and starts six hours back", hi == NOW and NOW - lo == 21600)
lo, hi = ta.window("365d", NOW)
check("'Past year' is 365 days", NOW - lo == 365 * 86400)
day = datetime.date.today() - datetime.timedelta(days=10)
lo, hi = ta.window("custom", NOW, f"{day}T08:00", f"{day}T17:30")
want_lo = datetime.datetime(day.year, day.month, day.day, 8).timestamp()
want_hi = datetime.datetime(day.year, day.month, day.day, 17, 30).timestamp()
check("'Pick dates...' is the exact start and end, local time",
      lo == want_lo and hi == want_hi, (lo, hi, want_lo, want_hi))
lo, hi = ta.window("custom", NOW, str(day), str(day))
check("a bare end date covers that whole day",
      hi - lo == 86400 or abs(hi - lo - 86400) == 3600, hi - lo)
lo, hi = ta.window("custom", NOW, str(day), "2099-01-01")
check("a future end charts up to now", hi == NOW)
check("no start is no window", ta.window("custom", NOW, "", "") == (None, None))

# -- 3. bucket edges -----------------------------------------------------------------
noon = datetime.datetime.now().replace(hour=13, minute=47, second=5, microsecond=0)
edge = datetime.datetime.fromtimestamp(ta.floor(noon.timestamp(), "6h"))
check("every 6 hours starts at 12:00 local for 13:47", (edge.hour, edge.minute) == (12, 0), edge)
edge = datetime.datetime.fromtimestamp(ta.floor(noon.timestamp(), "1d"))
check("a day starts at local midnight", (edge.hour, edge.minute) == (0, 0), edge)
edge = datetime.datetime.fromtimestamp(ta.floor(noon.timestamp(), "month"))
check("a month starts on the 1st", (edge.day, edge.hour) == (1, 0), edge)
edge = datetime.datetime.fromtimestamp(ta.floor(noon.timestamp(), "90m"))
check("a custom 90 minutes lands on a 90-minute edge from local midnight",
      (edge.hour * 60 + edge.minute) % 90 == 0, edge)
axis = ta.axis(noon.timestamp() - 2 * 86400, noon.timestamp(), "6h")
check("the axis is every step in the window, none missing",
      all(b - a == 21600 for a, b in zip(axis, axis[1:])) and 8 <= len(axis) <= 9, len(axis))

# -- 4. every step on a two-input chart from a workflow node -----------------------
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"pcb": {"key": "pcb", "name": "pcb", "columns": [
    {"name": "status", "type": "text"}, {"name": "total_panel", "type": "number"}]}}


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def graph(data, inputs=2):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("t1", "output.dataset", {"dataset": "pcb", "name": "CAM1",
                                           "mode": "change", "sources": {}}),
             node("t2", "output.dataset", {"dataset": "pcb", "name": "CAM2",
                                           "mode": "change", "sources": {}}),
             node("g", "dashboard.graph", {"name": "Panels", **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    for t in ("t1", "t2"):
        edges.append({"id": f"c{t}", "source": "fr", "target": t,
                      "targetHandle": "c_total_panel"})
    edges.append({"id": "w1", "source": "t1", "sourceHandle": "o_total_panel",
                  "target": "g", "targetHandle": "in"})
    if inputs == 2:
        edges.append({"id": "w2", "source": "t2", "sourceHandle": "o_total_panel",
                      "target": "g", "targetHandle": "s2"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         tables=TABLES)
    assert not c["errors"], c["errors"]
    w = next(x for x in c["widgets"] if x["id"] == "wf_g")
    return builder_service.validate([w])[0]


# The store: rows every 7 minutes over the last 40 days, camera 1 only (so
# camera 2 is the empty input), each row worth 3 panels.
ROWS = [NOW - i * 420 for i in range(int(40 * 86400 / 420))]
CALLS = []


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None):
    CALLS.append({"bucket": bucket, "since": since, "until": until, "agg": agg})
    lo = datetime.datetime.fromisoformat(since).timestamp() if since else 0
    hi = datetime.datetime.fromisoformat(until).timestamp() if until else NOW + 1
    if CALLS and len(CALLS) % 2 == 0:
        return {"buckets": []}                      # camera 2 recorded nothing
    cells = {}
    for ts in ROWS:
        if lo <= ts < hi:
            key = builder_service._bucket_key(ta.iso(ts), bucket)
            cells.setdefault(key, []).append(3.0)
    out = []
    for key in sorted(cells):
        vals = cells[key]
        out.append({"key": key, "value": float(len(vals)) if agg == "count"
                    else sum(vals) / len(vals)})
    return {"buckets": out}


datasets_repo.aggregate = fake_aggregate


def render(w):
    return builder_service.compute([w], [], {}, slug="smoke")[0]["data"]


CASES = [("1m", "3h"), ("30m", "24h"), ("1h", "24h"), ("6h", "7d"), ("12h", "7d"),
         ("1d", "30d"), ("90m", "24h"), ("2d", "30d")]
for step, rng in CASES:
    w = graph({"kind": "bar", "bucket": step, "range": rng, "agg": "count"})
    CALLS.clear()
    d = render(w)
    sets = d.get("datasets") or []
    keys = d.get("keys") or []
    # The window each input's query holds -- the compile keeps an older
    # node's "7d"/"30d" as the day-aligned "week"/"month" it always meant
    # (workflow._QUERY_RANGE_OF), so the expected start is read from it.
    q_range = (w["series"][0].get("query") or {}).get("range") or rng
    win_lo, win_hi = ta.window(q_range)
    span = win_hi - win_lo
    fitted, _grew = ta.fit(step, span)
    secs = ta.step_seconds(fitted)
    check(f"{step} over {rng}: the store is asked for that bucket and window",
          CALLS and all(c["bucket"] == ta.store_bucket(step) and c["since"] and c["until"]
                        for c in CALLS), CALLS[:1])
    check(f"{step} over {rng}: one axis, every step of the window",
          keys and len(sets) == 2 and all(len(s["values"]) == len(keys) for s in sets)
          and all(abs((b - a) / 1000 - secs) <= 3600 for a, b in zip(keys, keys[1:]))
          and abs(keys[0] / 1000 - ta.floor(win_lo, step)) < 1
          and d.get("step_s") == secs, (len(keys), d.get("step_s"), d.get("step")))
    check(f"{step} over {rng}: empty steps are zero for a count",
          all(v == 0 for v in sets[1]["values"]) and 0 not in sets[0]["values"][1:-1]
          or step == "1m", sets[0]["values"][:6])
    check(f"{step} over {rng}: titled by its step",
          d.get("step") == ta.step_words(step), d.get("step"))

# An average of nothing is a gap, not a zero.
w = graph({"kind": "line", "bucket": "1h", "range": "24h", "agg": "avg"})
d = render(w)
check("an average leaves an empty step as a gap",
      d["datasets"][1]["values"] and all(v is None for v in d["datasets"][1]["values"]),
      d["datasets"][1]["values"][:4])

# -- exact range --------------------------------------------------------------------
day = datetime.date.today() - datetime.timedelta(days=5)
w = graph({"kind": "bar", "bucket": "1h", "range": "custom",
           "range_from": f"{day}T06:00", "range_to": f"{day}T18:00"})
check("the compile carries the picked dates onto every input",
      all((s.get("query") or {}).get("range_from") == f"{day}T06:00" for s in w["series"]),
      [s.get("query") for s in w["series"]])
CALLS.clear()
d = render(w)
lo = datetime.datetime(day.year, day.month, day.day, 6).timestamp()
hi = datetime.datetime(day.year, day.month, day.day, 18).timestamp()
check("the exact range is the query's WHERE, not a trim afterwards",
      CALLS and datetime.datetime.fromisoformat(CALLS[0]["since"]).timestamp() == lo
      and datetime.datetime.fromisoformat(CALLS[0]["until"]).timestamp() == hi, CALLS[:1])
check("...and the axis is exactly those twelve hours",
      len(d["keys"]) == 12 and d["keys"][0] / 1000 == lo, (len(d["keys"]), d["keys"][:1]))

# -- 5. too many points ------------------------------------------------------------
w = graph({"kind": "line", "bucket": "1m", "range": "365d"})
CALLS.clear()
d = render(w)
check("a year every minute is widened to fit, and says so",
      len(d["keys"]) <= ta.MAX_POINTS and d.get("step_note", "").startswith("Showing every")
      and CALLS[0]["bucket"] != "60s", (len(d["keys"]), d.get("step_note"), CALLS[:1]))

# -- 6. a one-input chart -------------------------------------------------------------
w = graph({"kind": "area", "bucket": "6h", "range": "7d", "agg": "count"}, inputs=1)
d = render(w)
check("a one-input chart draws on the same axis",
      d.get("step_s") == 21600 and d.get("values") and len(d["values"]) == len(d["keys"]),
      {k: d.get(k) for k in ("step_s", "step")})

# -- 7. Builder -> node ------------------------------------------------------------------
store = {"workflows": [{"id": "w1", "graph": {"nodes": [node("g", "dashboard.graph", {
    "name": "Panels", "kind": "line"})], "edges": []}}]}
n = wf.apply_card_style(store, [{"id": "wf_g", "bucket": "90m", "range": "custom",
                                 "range_from": "2026-10-01T08:00", "range_to": "2026-10-02"}])
data = store["workflows"][0]["graph"]["nodes"][0]["data"]
check("a Builder's step and picked dates land on the node",
      n == 4 and data["bucket"] == "90m" and data["range"] == "custom"
      and data["range_from"] == "2026-10-01T08:00", data)
check("a custom step that cannot be stored is refused",
      wf.apply_card_style(store, [{"id": "wf_g", "bucket": "0m"}]) == 0
      and wf.apply_card_style(store, [{"id": "wf_g", "bucket": "91d"}]) == 0
      and data["bucket"] == "90m")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
