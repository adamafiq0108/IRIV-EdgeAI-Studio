# Whole-frame counters: live count, unique-by-PID total, per-day tallies that
# survive a restart, and Analytics' date range applied to them.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app.functions import Function, FunctionEngine, DAILY_FILE   # noqa: E402

DATA = tempfile.mkdtemp()
DAY = 86400


def det(tid, label="person"):
    return {"track_id": tid, "label": label,
            "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.3}


def engine():
    live = Function({"id": "now", "type": "frame_count",
                     "params": {"classes": ["person"]}})
    tot = Function({"id": "seen", "type": "frame_unique",
                    "params": {"classes": ["person"]}})
    return FunctionEngine([live, tot], state_dir=DATA)


# 1. live count is what is on camera THIS frame; total counts each PID once
eng = engine()
now = time.time()
eng.update({}, [det(1), det(2), det(9, "car")], {}, {}, 640, 480, now=now)
s = eng.stats()
assert s["fn_now_count"] == 2, s["fn_now_count"]          # the car is filtered
assert s["fn_seen_total"] == 2, s["fn_seen_total"]
eng.update({}, [det(1)], {}, {}, 640, 480, now=now)        # 2 walked out
s = eng.stats()
assert s["fn_now_count"] == 1 and s["fn_seen_total"] == 2, s
eng.update({}, [det(1), det(3)], {}, {}, 640, 480, now=now)  # a new person
s = eng.stats()
assert s["fn_now_count"] == 2 and s["fn_seen_total"] == 3, s
print("== live count follows the frame; total counts each PID once:",
      s["fn_now_count"], s["fn_seen_total"])

# 2. today / week / month and the by_day bucket
today = time.strftime("%Y-%m-%d", time.localtime(now))
assert eng.stats()["fn_seen_today"] == 3, eng.stats()["fn_seen_today"]
assert eng.stats()["fn_seen_by_day"] == {today: 3}, eng.stats()["fn_seen_by_day"]
print("== today/week/month published, by_day bucket:", eng.stats()["fn_seen_by_day"])

# 3. the tally is written to disk and reloaded -- a restart keeps "today"
eng.flush_daily(force=True)
path = os.path.join(DATA, DAILY_FILE)
assert os.path.isfile(path), "tallies were not persisted"
eng2 = engine()                                  # "restart"
eng2.update({}, [det(50)], {}, {}, 640, 480, now=now)
s2 = eng2.stats()
assert s2["fn_seen_today"] == 4, s2["fn_seen_today"]     # 3 kept + 1 new
assert s2["fn_seen_total"] == 1, s2["fn_seen_total"]     # identity is per-run
print("== a restart keeps the day tally (today=4) while live identity resets")

# 4. older days age into week/month, not today
eng2.flush_daily(force=True)
disk = json.load(open(path, encoding="utf-8"))
older = time.strftime("%Y-%m-%d", time.localtime(now - 3 * DAY))
way_back = time.strftime("%Y-%m-%d", time.localtime(now - 20 * DAY))
disk["seen"][older] = 5
disk["seen"][way_back] = 7
json.dump(disk, open(path, "w", encoding="utf-8"))
eng3 = engine()
eng3.update({}, [], {}, {}, 640, 480, now=now)
s3 = eng3.stats()
assert s3["fn_seen_today"] == 4, s3["fn_seen_today"]
assert s3["fn_seen_week"] == 9, s3["fn_seen_week"]        # 4 + 5
assert s3["fn_seen_month"] == 16, s3["fn_seen_month"]     # 4 + 5 + 7
print("== today/week/month split correctly:", s3["fn_seen_today"],
      s3["fn_seen_week"], s3["fn_seen_month"])

# 5. Analytics scopes the bucket AND the total to the chosen range
from core.services import analytics as an                  # noqa: E402
stats = {"fn_seen_by_day": s3["fn_seen_by_day"], "fn_seen_total": 999}
scoped = an.scope_stats(stats, "today")
assert scoped["fn_seen_total"] == 4, scoped["fn_seen_total"]
scoped = an.scope_stats(stats, "week")
assert scoped["fn_seen_total"] == 9, scoped["fn_seen_total"]
scoped = an.scope_stats(stats, "month")
assert scoped["fn_seen_total"] == 16, scoped["fn_seen_total"]
print("== Analytics range cuts the total to today/week/month: 4 / 9 / 16")

# 6. the master validates and compiles both types
from core.services import functions as fx                  # noqa: E402
for ftype in ("frame_count", "frame_unique"):
    clean = fx.validate_function({"id": f"x_{ftype}", "type": ftype,
                                  "params": {"classes": ["person"]}})
    assert clean["params"]["classes"] == ["person"], clean
print("== master accepts both function types")

# 7. workflow compiles the nodes; a Zone wired in is refused
from core.services import workflow as wf                   # noqa: E402
graph = {"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
     "data": {"classes": ["person"]}},
    {"id": "c1", "type": "analytics.frame_count", "position": {"x": 2, "y": 0},
     "data": {"fnId": "in_frame_now", "name": "In frame now"}},
    {"id": "t1", "type": "analytics.frame_total", "position": {"x": 2, "y": 1},
     "data": {"fnId": "in_frame_total", "name": "In frame total"}},
], "edges": [
    {"id": "e1", "source": "m1", "target": "f1"},
    {"id": "e2", "source": "f1", "target": "c1"},
    {"id": "e3", "source": "f1", "target": "t1"},
]}
out = wf.compile_graph(graph, [], ["person"], ["person"])
assert not out["errors"], out["errors"]
kinds = {f["id"]: f["type"] for f in out["functions"]}
assert kinds == {"in_frame_now": "frame_count",
                 "in_frame_total": "frame_unique"}, kinds
print("== workflow compiles both nodes:", kinds)

ok, msg = wf.edge_allowed("zone.ref", "analytics.frame_total")
assert not ok and "whole frame" in msg, (ok, msg)
print("== a Zone into a frame counter is refused:", msg[:60])
print("ALL OK")
