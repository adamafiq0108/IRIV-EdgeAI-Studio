# The clock as a rule.
#
# Everything else on the canvas answers "something happened". A report that
# goes out at noon needs the other question -- "the time came round" -- and
# there was no node that could say it: the Email node's hours/days fields only
# narrow WHEN a rule may send, they never fire on their own. So "send me a
# summary every day at 12:00" was unbuildable.
#
# A Schedule arms itself the way a Formula does: 12:00 is not a threshold
# somebody chose, so demanding a Condition (or an Alert -- which means
# something is WRONG) after it would be asking the operator to write down a
# decision the clock already made.
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fs                     # noqa: E402
from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the node exists, and it is one node with a selector -------------------
reg = wf.registry_public()
spec = reg.get("variable.schedule") or {}
check("the palette offers a Schedule", bool(spec) and not spec.get("deprecated"))
check("...as ONE node, not Daily/Weekly/Monthly as three",
      not any(k for k in reg if k.startswith("variable.schedule")
              and k != "variable.schedule"))
check("...filed in its own palette drawer, in the input stage",
      (spec.get("palette") or {}).get("cat") == "time")
check("...wired as a variable, so it may reach an output with no new edge rule",
      spec.get("category") == "variable"
      and "output" in wf._ALLOWED_TARGETS["variable"])
check("...and it publishes when it is due, how many runs, and the next one",
      spec.get("metrics") == ["due", "runs", "next_s"]
      and fs.OUTPUTS["schedule"] == ("due", "runs", "next_s"))


# -- 2. it compiles straight into an Email: no Condition, no Alert ------------
def graph(data, node_type="output.email",
          out_data=None):
    return wf.clean_graph({"nodes": [
        {"id": "s1", "type": "variable.schedule", "position": {"x": 0, "y": 0},
         "data": {"fnId": "nightly", "name": "Nightly", **data}},
        {"id": "o1", "type": node_type, "position": {"x": 1, "y": 0},
         "data": out_data or {"to": "boss@example.com", "subject": "Daily",
                              "body": "{people} in the bay today"}},
    ], "edges": [{"id": "e1", "source": "s1", "target": "o1"}]})


c = wf.compile_graph(graph({"every": "daily", "at": "12:00"}), [])
check("a Schedule wired straight into an Email compiles", not c["errors"],
      c["errors"])
fn = [f for f in c["functions"] if f["id"] == "nightly"]
check("...as a schedule function", len(fn) == 1 and fn[0]["type"] == "schedule",
      c["functions"])
if fn:
    p = fn[0]["params"]
    check("...carrying what the operator set", p["every"] == "daily"
          and p["at"] == "12:00", p)
    check("...and NOT a camera: a clock is not about a lens",
          "camera" not in p and "model" not in p, p)
    trig = (fn[0].get("triggers") or [fn[0].get("trigger")])[0]
    check("...armed by itself, on `due`, with no cooldown to swallow a run",
          trig and trig["metric"] == "due" and trig["cooldown_s"] == 0, trig)
out = c["outputs"][0] if c["outputs"] else {}
check("the email fires on THIS schedule only",
      out.get("trigger") == "alert" and out.get("function") == "nightly", out)

# A Schedule needs no Detection Stream: a report is not about the camera.
check("...and it is not switched off for want of a camera feed",
      "schedule" in wf.STANDALONE_FN_TYPES
      and not any("Detection Stream" in w["message"] for w in c["warnings"]),
      c["warnings"])

# Telegram, same shape.
c = wf.compile_graph(graph({"every": "weekly", "weekday": 0, "at": "08:00"},
                           "output.telegram",
                           {"chat_id": "123", "message": "weekly"}), [])
check("a Schedule into Telegram compiles the same way",
      not c["errors"] and c["outputs"][0]["type"] == "telegram", c["errors"])

# -- 2b. the shape the node EXISTS for: the clock says when, counters say what
# A Schedule alone sends an empty report. The real graph is a schedule and two
# counters into one Email -- and that failed to compile at first, because the
# compiler only counted Conditions, branches and Triggers as things that say
# WHEN, so a Schedule read as a third ordinary value and the node refused with
# "wire ONE Condition into it" on a graph whose condition is the clock.
ZONES = [{"id": "z1", "name": "Bay", "kind": "area", "enabled": True}]


def report_graph(schedules=1, out_type="output.email", out_data=None):
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "z1"}},
        {"id": "cnt", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "people", "name": "People"}},
        {"id": "tot", "type": "analytics.frame", "position": {"x": 2, "y": 1},
         "data": {"fnId": "seen", "name": "Seen", "mode": "realtime"}},
        {"id": "o1", "type": out_type, "position": {"x": 3, "y": 0},
         "data": out_data or {"to": "boss@example.com", "subject": "Daily",
                              "body": "{people} in the bay, {seen} seen"}},
    ]
    edges = [{"id": "e1", "source": "src", "target": "z"},
             {"id": "e2", "source": "z", "target": "cnt"},
             {"id": "e3", "source": "src", "target": "tot"},
             {"id": "e4", "source": "cnt", "target": "o1"},
             {"id": "e5", "source": "tot", "target": "o1"}]
    for i in range(schedules):
        nodes.append({"id": f"s{i}", "type": "variable.schedule",
                      "position": {"x": 2, "y": 2 + i},
                      "data": {"fnId": f"nightly{i}", "name": f"Nightly {i}",
                               "every": "daily", "at": "12:00"}})
        edges.append({"id": f"es{i}", "source": f"s{i}", "target": "o1"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


c = wf.compile_graph(report_graph(), ZONES, ["person"], ["person"])
check("Schedule + two counters into one Email COMPILES", not c["errors"],
      c["errors"])
out = c["outputs"][0] if c["outputs"] else {}
check("...and the Email fires on the SCHEDULE, not on a counter",
      out.get("function") == "nightly0" and out.get("trigger") == "alert", out)
check("...while both counters become placeholders its text can quote",
      (out.get("vars") or {}).get("people") == "fn_people_count"
      and (out.get("vars") or {}).get("seen") == "fn_seen_count",
      out.get("vars"))
# The clock is not a value anybody would put in a sentence: `due` is 1 on the
# tick the message goes out, so offering {nightly_0} would offer a "1".
check("...and the schedule itself is NOT offered as a placeholder",
      not any(k.endswith("_due") for k in (out.get("vars") or {}).values()),
      out.get("vars"))

c = wf.compile_graph(report_graph(out_type="output.telegram",
                                  out_data={"chat_id": "1", "message": "{people}"}),
                     ZONES, ["person"], ["person"])
check("...the same graph into Telegram behaves identically",
      not c["errors"] and c["outputs"][0].get("function") == "nightly0",
      c["errors"])

# Two clocks on one message is a question only the operator can answer: it
# reads as "send it at both times", which is two Emails.
c = wf.compile_graph(report_graph(schedules=2), ZONES, ["person"], ["person"])
msg = " ".join(e["message"] for e in c["errors"])
check("two Schedules into one Email is refused", bool(c["errors"]), c["errors"])
check("...and the refusal names the Schedule, not only the Condition",
      "Schedule" in msg and "WHEN" in msg, msg)

# A Condition and a Schedule both saying WHEN is the same ambiguity.
mixed = report_graph()
mixed["nodes"].append({"id": "w1", "type": "logic.when",
                       "position": {"x": 2, "y": 5},
                       "data": {"metric": "count", "mode": "above", "value": 2}})
mixed["edges"].append({"id": "ew", "source": "cnt", "target": "w1"})
mixed["edges"].append({"id": "ew2", "source": "w1", "target": "o1"})
c = wf.compile_graph(wf.clean_graph(mixed), ZONES, ["person"], ["person"])
check("a Condition AND a Schedule on one Email is refused too", bool(c["errors"]),
      c["errors"])

# -- 3. every repeat the selector offers survives the compiler ----------------
for data, want in (({"every": "minutes", "interval_min": 15}, {"interval_min": 15}),
                   ({"every": "hourly", "minute": 30}, {"minute": 30}),
                   ({"every": "daily", "at": "9:05"}, {"at": "09:05"}),
                   ({"every": "weekly", "weekday": 4, "at": "17:00"},
                    {"weekday": 4, "at": "17:00"}),
                   ({"every": "monthly", "day": 31, "at": "08:00"},
                    {"day": 31, "at": "08:00"})):
    c = wf.compile_graph(graph(data), [])
    got = c["functions"][0]["params"] if c["functions"] else {}
    check(f"{data['every']}: compiles and keeps its fields",
          not c["errors"] and all(got.get(k) == v for k, v in want.items()),
          f"{c['errors']} {got}")

# A time that is not a time is refused with a sentence, not a traceback.
c = wf.compile_graph(graph({"every": "daily", "at": "half past"}), [])
check("a time nobody can read is refused where the operator can see it",
      any("Schedule" in e["message"] for e in c["errors"]), c["errors"])

# -- 4. the ENGINE: what the clock actually does ------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import functions as appfn                            # noqa: E402

check("the app engine knows the type", "schedule" in appfn.TYPES)

DAY = 86400.0


def at(y, mo, d, h, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


prev, nxt = appfn._schedule_bounds({"every": "daily", "at": "12:00"},
                                   at(2026, 9, 7, 13, 0))
check("daily at 12:00 read at 13:00: noon today, then noon tomorrow",
      prev == at(2026, 9, 7, 12) and nxt == at(2026, 9, 8, 12),
      f"{prev} {nxt}")
prev, nxt = appfn._schedule_bounds({"every": "daily", "at": "12:00"},
                                   at(2026, 9, 7, 9, 0))
check("...read at 09:00: YESTERDAY's noon, and today's is still to come",
      prev == at(2026, 9, 6, 12) and nxt == at(2026, 9, 7, 12),
      f"{prev} {nxt}")

# Monthly on the 31st in a 30-day month runs on the 30th: a report that
# silently never goes out in April is worse than one a day early.
prev, _ = appfn._schedule_bounds({"every": "monthly", "day": 31, "at": "08:00"},
                                 at(2026, 4, 30, 9, 0))
check("the 31st of a 30-day month is that month's LAST day",
      prev == at(2026, 4, 30, 8), prev)
prev, _ = appfn._schedule_bounds({"every": "monthly", "day": 31, "at": "08:00"},
                                 at(2026, 3, 1, 9, 0))
check("...and a month that HAS a 31st uses it",
      prev == at(2026, 2, 28, 8), prev)
# "The last day of the month" is a thing operators say, so it is a choice
# rather than a trick with the number 31.
prev, _ = appfn._schedule_bounds({"every": "monthly", "day": 0, "at": "08:00"},
                                 at(2026, 2, 20, 9, 0))
check("day 0 means the LAST day, whatever length the month is",
      prev == at(2026, 1, 31, 8), prev)
c = wf.compile_graph(graph({"every": "monthly", "day": 0, "at": "08:00"}), [])
check("...and it survives the compiler as a real choice",
      not c["errors"] and c["functions"][0]["params"]["day"] == 0,
      f"{c['errors']} {c['functions']}")

prev, _ = appfn._schedule_bounds({"every": "weekly", "weekday": 0, "at": "08:00"},
                                 at(2026, 9, 7, 9, 0))   # 2026-09-07 is a Monday
check("weekly on Monday, read Monday morning: today's run",
      prev == at(2026, 9, 7, 8), prev)

prev, nxt = appfn._schedule_bounds({"every": "minutes", "interval_min": 15},
                                   at(2026, 9, 7, 10, 7))
check("every 15 minutes lands on the quarter hour, not on start-up time",
      prev == at(2026, 9, 7, 10, 0) and nxt == at(2026, 9, 7, 10, 15),
      f"{prev} {nxt}")


def engine(params, state_dir=None):
    spec = {"id": "nightly", "name": "Nightly", "type": "schedule",
            "params": {"every": "daily", "at": "12:00", **params},
            "triggers": [{"metric": "due", "op": ">", "value": 0.0,
                          "severity": "info", "message": "Nightly",
                          "cooldown_s": 0}]}
    return appfn.FunctionEngine(appfn.parse_functions([spec]),
                                state_dir=state_dir)


def tick(eng, now):
    return eng.update({}, [], {}, {}, 640, 480, zones=[], now=now)


eng = engine({})
fired = tick(eng, at(2026, 9, 7, 11, 59))
check("a schedule dropped on the canvas before noon does not fire on sight",
      not fired and eng.stats()["fn_nightly_due"] == 0, fired)
fired = tick(eng, at(2026, 9, 7, 12, 0))
check("...fires the tick noon arrives",
      len(fired) == 1 and fired[0]["message"] == "Nightly", fired)
check("...and says so as `due` for that one tick",
      eng.stats()["fn_nightly_due"] == 1)
fired = tick(eng, at(2026, 9, 7, 12, 0) + 1)
check("...then goes quiet: one report, not one per frame",
      not fired and eng.stats()["fn_nightly_due"] == 0, fired)
fired = tick(eng, at(2026, 9, 8, 12, 0))
check("...and comes round again tomorrow", len(fired) == 1, fired)
check("the node can be checked without waiting for it: next_s counts down",
      0 < eng.stats()["fn_nightly_next_s"] <= DAY,
      eng.stats().get("fn_nightly_next_s"))
check("...and how many runs have gone out is published",
      eng.stats()["fn_nightly_runs"] == 2, eng.stats().get("fn_nightly_runs"))

# A run missed while the box was OFF. Two different questions, so two answers.
eng = engine({})
tick(eng, at(2026, 9, 7, 11, 0))            # adopts the current period
fired = tick(eng, at(2026, 9, 7, 18, 0))    # box was down over noon
check("a missed run is dropped by default: no 6pm report nobody asked for",
      not fired, fired)
eng = engine({"catch_up": True})
tick(eng, at(2026, 9, 7, 11, 0))
fired = tick(eng, at(2026, 9, 7, 18, 0))
check("...and goes out on return when the operator asked for that",
      len(fired) == 1, fired)

# -- 5. a restart at noon must not send the report twice ----------------------
state = tempfile.mkdtemp()
eng = engine({}, state)
tick(eng, at(2026, 9, 7, 11, 59))
check("the first tick fires nothing", not eng.stats()["fn_nightly_due"])
fired = tick(eng, at(2026, 9, 7, 12, 0))
check("noon fires once", len(fired) == 1, fired)
again = engine({}, state)                   # the app restarts, one second later
fired = tick(again, at(2026, 9, 7, 12, 0) + 1)
check("a restart at 12:00:01 does NOT send the noon report again",
      not fired, fired)
check("...because the run is remembered on disk the moment it goes out",
      os.path.exists(os.path.join(state, appfn.SCHEDULE_FILE)))
check("...and the run count survives with it",
      again.stats()["fn_nightly_runs"] == 1, again.stats())

# A corrupt file costs one duplicate report, never a start-up.
io.open(os.path.join(state, appfn.SCHEDULE_FILE), "w",
        encoding="utf-8").write("{not json")
eng = engine({}, state)
check("a corrupt schedule file still lets the app start", eng is not None)

# -- 6. the sentence on the node and in the form come from one place ----------
src = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
              encoding="utf-8").read()
page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
               encoding="utf-8").read()
check("the node says what it does in words",
      "export function scheduleSentence(d)" in src)
check("...and the form says the same, from the same function",
      "scheduleSentence(d)" in page and "scheduleSentence, WEEKDAYS" in page)
check("the form is one Runs selector, not five nodes",
      "function ScheduleFields(" in page
      and page.count('onChange({ every: e.target.value })') == 1)
check("the palette draws the new drawer",
      '"board", "time",' in page)
# A Schedule set to 12:00 means noon WHERE THE BOX IS, and the browser reading
# the canvas may be in another country. So the zone is named rather than left
# for the operator to assume, and it comes from the box, never from the page.
routes = io.open(os.path.join(ROOT, "core", "routes", "workflow.py"),
                 encoding="utf-8").read()
check("the box tells the page which clock it keeps",
      "def _box_clock()" in routes and '"clock": _box_clock(),' in routes)
check("...reading the zone NAME the OS stores, not Python's \"+08\"",
      "/etc/timezone" in routes and "zoneinfo/" in routes)
check("...and the form says so beside the time",
      "box clock, ${clock.zone}" in page)
check("the next run is shown as a time, not only as a countdown",
      "export function nextRunAt(seconds, clock)" in src
      and "nextRunAt(nextRun, clock)" in src)
check("...anchored to the box's clock, not the reader's",
      "clock?.now" in src)
check("'Every N minutes', not 'every few'",
      ">Every N minutes<" in page)
check("the last day of the month is a choice, not a trick with 31",
      "The last day of the month" in page)
check("a missed run is advanced, and says what it would send",
      "Advanced - a run missed while the box was off" in page
      and "not as they were at the missed" in page)
check("...with a style of its own", "time:      { color:" in
      io.open(os.path.join(ROOT, "frontend", "src", "workflow", "registry.js"),
              encoding="utf-8").read())

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
