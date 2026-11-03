"""A footage export that repeats on its own.

Emailing evidence was a manual act. Right for "send me this morning", wrong
for a shift handover or a weekly record: those want to arrive without anybody
remembering to make them, and a person who has to remember is a person who
forgets in the week it mattered.

Almost all of this file is the period arithmetic, because that is where a
scheduler goes wrong quietly. The rules it pins:

  * an export covers the period that CLOSED, not "the last 24 hours" from the
    moment it fires -- a window that slides with the clock overlaps its
    neighbours, so two daily reports share hours and a gap is invisible;
  * a box that was off at 08:00 still sends when it comes back, because the
    boundary it missed is still the most recent one;
  * a master that restarts does NOT send the same period twice, which is why
    the mark is on disk rather than in memory;
  * "the last day of the month" is a real answer, and day 31 in February is
    the 28th rather than a report that vanishes eleven times a year.

    python tests/smoke_footageschedule.py
"""
import datetime
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import footage_schedule as fs             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def at(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


def when(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def sched(**over):
    return {**fs.DEFAULTS, **over}


# -- 1. which period closed --------------------------------------------------
# Wednesday 4 March 2026, 09:30.
now = at("2026-03-04 09:30")

s = sched(every="daily", at="08:00")
check("a daily whose time has passed covers up to TODAY 08:00",
      when(fs.period_end(s, now)) == "2026-03-04 08:00", when(fs.period_end(s, now)))
check("...and the window before it is the previous day",
      when(fs.period_start(s, fs.period_end(s, now))) == "2026-03-03 08:00")
early = at("2026-03-04 07:00")
check("...while before the time it is still YESTERDAY's boundary",
      when(fs.period_end(s, early)) == "2026-03-03 08:00", when(fs.period_end(s, early)))

s = sched(every="hourly", minute=15)
check("hourly closes at the last quarter past",
      when(fs.period_end(s, now)) == "2026-03-04 09:15", when(fs.period_end(s, now)))
check("...and covers the hour before it",
      when(fs.period_start(s, fs.period_end(s, now))) == "2026-03-04 08:15")
check("...stepping back an hour when the minute has not arrived",
      when(fs.period_end(sched(every="hourly", minute=45), now))
      == "2026-03-04 08:45")

# Monday is 2 March 2026.
s = sched(every="weekly", weekday=0, at="08:00")
check("a weekly on Monday, read on Wednesday, closed on Monday",
      when(fs.period_end(s, now)) == "2026-03-02 08:00", when(fs.period_end(s, now)))
check("...and covers the seven days before it",
      when(fs.period_start(s, fs.period_end(s, now))) == "2026-02-23 08:00")
# Read ON the day but before the time: the boundary is a week back, not today.
s_fri = sched(every="weekly", weekday=2, at="10:00")     # Wednesday 10:00
check("...a weekly whose own day is today but time has not come waits a week",
      when(fs.period_end(s_fri, now)) == "2026-02-25 10:00",
      when(fs.period_end(s_fri, now)))

s = sched(every="monthly", day=1, at="08:00")
check("a monthly on the 1st, read on the 4th, closed on the 1st",
      when(fs.period_end(s, now)) == "2026-03-01 08:00", when(fs.period_end(s, now)))
check("...covering the month before it",
      when(fs.period_start(s, fs.period_end(s, now))) == "2026-02-01 08:00")
check("...and one whose day has not come yet closed LAST month",
      when(fs.period_end(sched(every="monthly", day=20, at="08:00"), now))
      == "2026-02-20 08:00")

# The month-end cases, which is where a naive scheduler loses reports.
check("day 0 means the last day, whatever length the month is",
      when(fs.period_end(sched(every="monthly", day=0, at="23:00"),
                         at("2026-03-04 09:30"))) == "2026-02-28 23:00")
check("...and day 31 in February is the 28th, not a month with no report",
      when(fs.period_end(sched(every="monthly", day=31, at="08:00"),
                         at("2026-03-01 09:00"))) == "2026-02-28 08:00")

q = sched(every="months", every_n=3, day=1, at="08:00")
check("every 3 months steps back a quarter for its window",
      when(fs.period_start(q, fs.period_end(q, now))) == "2025-12-01 08:00",
      when(fs.period_start(q, fs.period_end(q, now))))

# -- 2. when it actually fires ------------------------------------------------
s = sched(enabled=True, to="a@b.c", every="daily", at="08:00", last_run=0)
window = fs.due(s, now)
check("a schedule that has never run is due", window is not None)
check("...for the period that closed", window and when(window[1]) == "2026-03-04 08:00")

s["last_run"] = fs.period_end(s, now)
check("...and is not due again for the same period",
      fs.due(s, now) is None,
      "a restarted master must not re-send the morning's export")
check("...but IS due once the next boundary passes",
      fs.due(s, at("2026-03-05 08:01")) is not None)
# The mark is the period END, not "when we last ran", which is what makes a
# box that was off at 08:00 still send when it comes back.
s["last_run"] = at("2026-03-03 08:00")
late = fs.due(s, at("2026-03-04 17:00"))
check("a box that was off at the send time still sends when it returns",
      late is not None and when(late[1]) == "2026-03-04 08:00", late)

check("a disabled schedule is never due",
      fs.due(sched(enabled=False, to="a@b.c"), now) is None)
check("...and neither is one with nobody to send to",
      fs.due(sched(enabled=True, to=""), now) is None)

# -- 2b. two channels, chosen independently -----------------------------------
# Wanting the record in an inbox AND on the shift group's phone is one
# request, not two exports; either alone is the common case.
check("Telegram alone is a destination",
      fs.due(sched(enabled=True, send_email=False, send_telegram=True,
                   chat_id="-100123"), now) is not None)
check("...email switched off with only an address is not",
      fs.due(sched(enabled=True, send_email=False, to="a@b.c"), now) is None,
      "a channel that is off must not keep the schedule alive")
check("...nor is Telegram switched on with no chat",
      fs.due(sched(enabled=True, send_email=False, send_telegram=True,
                   chat_id=""), now) is None)

# -- 3. what a browser may set ------------------------------------------------
# This arrives from a browser and then runs unattended for months, so "the UI
# would never send that" is not a check.
tmp = tempfile.mkdtemp()
out = fs.save({"every": "nonsense", "every_n": 999, "at": "99:99",
               "weekday": 12, "day": 55, "to": "x@y.z"}, "sys1", tmp)
check("an unknown period falls back to daily", out["every"] == "daily", out["every"])
check("...the stride is capped", out["every_n"] == fs.MAX_EVERY_N, out["every_n"])
check("...a bad time keeps the old one", out["at"] == fs.DEFAULTS["at"], out["at"])
check("...and the weekday and day are clamped",
      out["weekday"] == 6 and out["day"] == 31, out)

# The run marks are the record of what HAPPENED. A client that could rewrite
# them could make an export send twice, or never again.
out = fs.save({"last_run": 0, "last_result": "ha"}, "sys1", tmp)
check("a caller cannot rewrite the run marks",
      out["last_result"] != "ha", out["last_result"])

# Turning it on must not immediately post the period that already closed:
# saving "weekly, Mondays" on a Wednesday would otherwise send last week.
fresh = fs.save({"enabled": True, "to": "x@y.z", "every": "weekly"}, "sys2", tmp)
check("switching a schedule ON adopts the current period without sending",
      fs.due(fresh) is None, fresh)

# -- 4. the runner ------------------------------------------------------------
master = read("master_server.py")
check("the master runs the export loop", "_footage_export_loop(app)" in master
      and "asyncio.create_task(_footage_export_loop(app))" in master)
loop = master.split("async def _footage_export_loop(app):")[1].split("\nasync def ")[0]
check("...marking the period done whether it sent or not",
      "sched_service.mark_ran(slug, end, result, config_dir)" in loop,
      "a failing mailbox would otherwise retry every minute for ever")
check("...and saying nothing when the period was quiet",
      "nothing recorded in that period" in loop,
      "an email saying '0 files' every morning is how a report becomes noise")
check("...off the event loop, because zipping and SMTP both block",
      loop.count("asyncio.to_thread") == 3)
# A Telegram group being unreachable must not cancel the email, and one
# "sent" covering a half-completed send is a report an operator trusts and
# should not.
check("...attempting each channel separately, and saying so separately",
      'said.append(("email: " if ok else "email failed: ") + msg)' in loop
      and "footage_mail_service.send_telegram" in loop)

fm = read("core", "services", "footage_mail.py")
# Telegram uploads one document per request, so "separate files" there would
# be two hundred round trips and two hundred notifications on a phone.
check("Telegram always gets the zip, whatever the attach choice says",
      "Always a zip, never separate files" in fm)
check("...against its OWN size limit, not the mail server's",
      "def check_telegram(rows):" in fm and "MAX_TELEGRAM_BYTES" in fm)
check("...staged on disk rather than built in RAM",
      "tempfile.mkstemp" in fm.split("def send_telegram(")[1])

manual = read("core", "routes", "models.py").split(
    'name="detector.footage_email"')[1].split("\ndef _epoch(")[0]
check("the manual send offers the same two channels",
      "footage_mail_service.send_telegram" in manual)
check("...refusing only when NOTHING got through",
      "if not any_ok:" in manual,
      "one channel failing beside one that worked is not a failed send")

routes = read("core", "routes", "models.py")
check("saving and sending are different endpoints",
      'detector.footage_schedule_put' in routes
      and 'detector.footage_email' in routes)
save_route = routes.split('name="detector.footage_schedule_put"')[1]
check("...and saving sends nothing",
      "footage_mail_service.send" not in save_route.split("@router")[0],
      "an operator adjusting the time would post last month's evidence")
check("both are the export permission",
      save_route.count('perm.require(request, "footage.export")') == 1)

ui = read("frontend", "src", "components", "EmailFootageDialog.jsx")
check("the dialog offers Save beside Send",
      "saveSchedule(true)" in ui and ">Send now<" in ui.replace("\n", "")
      or "Send now" in ui)
check("...and a way to stop it",
      "saveSchedule(false)" in ui and "Stop auto" in ui)
check("...measuring the period the schedule COVERS, not the date boxes",
      "start: window_[0], end: window_[1]" in ui)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
