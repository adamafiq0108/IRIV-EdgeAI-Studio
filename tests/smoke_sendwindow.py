"""The send window belongs to the clock, not to each message.

An Email node and a Telegram node each carried their own "when it may send":
a cooldown, a daily window, weekdays, a date range. Three problems with that,
and the third is the one that cost a real report:

  * it answered "when" on the node whose job is "what to say";
  * a rule that emails AND messages needed the same window typed twice and
    kept in step by hand;
  * it silently muted things. A schedule set to 09:45 with a window of
    09:43-09:45 fired exactly on time and sent nothing, because a window's
    upper bound is exclusive -- the report was suppressed by one minute, with
    no error anywhere, and the only evidence was an email that never arrived.

So the window moved onto the Schedule node, which is the node that already
answers "when". The cooldown did not move: on a clock it would be a second
pacing beside the interval, which is the one thing a schedule already is.

    python tests/smoke_sendwindow.py
"""
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fns                    # noqa: E402
from core.services import workflow as wf                      # noqa: E402
from app import functions as app_fns                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the message nodes no longer carry one ---------------------------------
for node in ("output.email", "output.telegram"):
    data = wf.REGISTRY[node]["data"]
    check(f"{node} carries no send window",
          not any(k in data for k in ("cooldown_s", "hours_from", "hours_to",
                                      "days", "from_date", "to_date")),
          sorted(data))

# The compiled document is built from spec["data"], so dropping the keys there
# is what stops them being written -- and this pins that they really are gone
# from what reaches the app.
dc_src = read("core", "services", "detector_control.py")
check("...and a compiled output no longer stores one",
      "        _keep_schedule(o, clean)\n" not in dc_src)
# The helper stays: a config written by hand, or written before the move and
# not yet re-applied, may still carry a window and the app still honours it.
# Deleting it would silently un-mute those.
check("...though the runtime still honours one it FINDS",
      "def _keep_schedule(o, clean):" in dc_src
      and "_message_allowed" in read("systems", "demo", "app", "outputs.py"))

ui = read("frontend", "src", "pages", "Workflow.jsx")
check("the editor offers it on the clock, not on the message",
      "function RunWindow(" in ui and "SendWindow" not in ui)
check("...rendered by the Schedule node",
      "<RunWindow d={d} set={set} disabled={!editable} />" in ui)
check("...and the cooldown went with it",
      'label="At most one message every"' not in ui,
      "a cooldown on a clock is a second pacing beside the interval")

# -- 2. the Schedule node carries it instead ----------------------------------
sched = wf.REGISTRY["variable.schedule"]["data"]
for key in ("hours_from", "hours_to", "days", "from_date", "to_date"):
    check(f"the Schedule node has {key}", key in sched, sorted(sched))

graph = wf.clean_graph({"nodes": [
    {"id": "s", "type": "variable.schedule", "position": {"x": 0, "y": 0},
     "data": {"fnId": "sched", "name": "Nightly", "every": "daily",
              "at": "22:00", "hours_from": "22:00", "hours_to": "06:00",
              "days": "mon,tue,wed,thu,fri", "from_date": "2026-01-01"}}],
    "edges": []})
c = wf.compile_graph(graph, [])
fn = next((f for f in c["functions"] if f["id"] == "sched"), None)
check("a compiled schedule carries its window", fn is not None, c["errors"])
p = (fn or {}).get("params") or {}
check("...the hours", (p.get("hours_from"), p.get("hours_to")) == ("22:00", "06:00"), p)
check("...the days, in week order rather than the order they were clicked",
      p.get("days") == "mon,tue,wed,thu,fri", p.get("days"))
check("...and the date", p.get("from_date") == "2026-01-01", p)

# Nothing is a restriction unless it PARSES: the failure mode of a bad window
# is a silently muted report, found out by the thing it was watching for
# happening unannounced.
junk = fns.clean_window({"hours_from": "9am", "to_date": "01/02/2026",
                         "days": "funday"})
check("a window that does not parse is DROPPED, never read as 'never'",
      junk == {}, junk)
check("...and all seven days is stored as no restriction at all",
      "days" not in fns.clean_window({"days": "mon,tue,wed,thu,fri,sat,sun"}))

# -- 3. the app honours it ------------------------------------------------------
def at(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


night = {"hours_from": "22:00", "hours_to": "06:00"}
check("a window that crosses midnight MEANS it: 23:00 is inside",
      app_fns._within_window(night, at("2026-03-04 23:00")))
check("...and so is 02:00", app_fns._within_window(night, at("2026-03-04 02:00")))
check("...while noon is not", not app_fns._within_window(night, at("2026-03-04 12:00")))

# The exact shape of the bug that started this: 09:43-09:45 with a schedule at
# 09:45. The upper bound is exclusive, so the report fired and was suppressed.
edge = {"hours_from": "09:43", "hours_to": "09:45"}
check("the upper bound is exclusive -- the case that muted a real report",
      not app_fns._within_window(edge, at("2026-03-04 09:45")),
      "unchanged behaviour; it is why the window left the Email node")
check("...and 09:44 is inside it",
      app_fns._within_window(edge, at("2026-03-04 09:44")))

# lo == hi is an UNSET window, not an empty one: reading a typo as "never"
# would mute the schedule.
check("a zero-length window is unset, not never",
      app_fns._within_window({"hours_from": "09:00", "hours_to": "09:00"},
                             at("2026-03-04 15:00")))
check("weekdays are honoured",
      app_fns._within_window({"days": "wed"}, at("2026-03-04 12:00"))
      and not app_fns._within_window({"days": "mon"}, at("2026-03-04 12:00")))
check("dates are inclusive at both ends",
      app_fns._within_window({"from_date": "2026-03-04",
                              "to_date": "2026-03-04"}, at("2026-03-04 12:00"))
      and not app_fns._within_window({"to_date": "2026-03-03"},
                                     at("2026-03-04 12:00")))
check("junk in a window is ignored rather than muting the schedule",
      app_fns._within_window({"hours_from": "nonsense", "days": "",
                              "from_date": "??"}, at("2026-03-04 12:00")))

app_src = read("systems", "demo", "app", "functions.py")
# A catch-up run for last night's 22:00 belongs to 22:00. Judging it by the
# hour the box came back would drop exactly the report catch_up exists for.
check("the window is judged against the moment that came DUE, not 'now'",
      "_within_window(fn.params, prev)" in app_src)
# A moment outside the window has PASSED, it did not fail. Leaving the period
# unconsumed would fire it the instant the window opened -- a queue of every
# missed period rather than a schedule.
sched_fn = app_src.split("def _update_schedule(")[1].split("\n    def ")[0]
check("...and the period is consumed either way",
      sched_fn.count('st["last"] = prev') == 2, sched_fn)
check("...while `runs` counts only what went out",
      'st["runs"] += 1          # runs counts what WENT OUT' in sched_fn)

# -- 4. proving a message can send at all ---------------------------------------
# Everything between writing a message and reading it is invisible, and every
# step of it fails silently at whatever hour the rule trips.
route = read("core", "routes", "workflow.py").split(
    '@router.post("/api/workflow/test-output"')[1]
check("a node can be told to send one now",
      "perm.require(request, \"workflow.edit\")" in route)
# A message built from the request body would let a caller name their own SMTP
# host and recipients and have this box send it.
check("...built from the SAVED canvas, never from the request",
      "compile_store(" in route and "no such" not in route.split("body")[0][:0]
      and 'detail="which node?"' in route)
check("...and only from a node that really is a message",
      'doc.get("type") not in ("email", "telegram")' in route)
check("the editor offers the button on both message nodes",
      ui.count("<TestSend node={node} disabled={!editable} />") == 2)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
