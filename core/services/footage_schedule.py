"""A recurring footage export: the same dialog, saved and left to run.

Emailing evidence was a manual act -- pick a range, press Send. That is right
for "send me what happened this morning" and wrong for the thing operators
actually asked for, which is a shift handover or a weekly record that arrives
without anybody remembering to make it. A person who has to remember is a
person who forgets on the week it mattered.

So the dialog can now be SAVED as well as sent. The saved form answers three
questions the manual one never had to:

  * WHICH PERIOD. A recurring export covers the period that just ENDED --
    yesterday for a daily, last week for a weekly. Not "the last 24 hours"
    from the moment it fires: a daily report whose window slides with the
    clock overlaps its neighbours and belongs to no day in particular, which
    makes two of them impossible to compare and a gap impossible to see.
  * WHEN it goes. A time on the local clock, which is the clock the operator
    set and the one every other time on this product means.
  * WHETHER IT ALREADY WENT. Persisted, not held in memory: a master that
    restarts must not re-send Monday's export, and a box that was off at
    08:00 should still send when it comes back rather than skipping the week.

Storage is one local TOML per system, like the digest settings next door
(`footage_export_<slug>.local.toml`), so a box with two systems keeps two
schedules and neither can send the other's evidence.
"""
import calendar
import datetime
import time

from core import config

#: How often an export repeats. "months" carries `every_n` so "every quarter"
#: is one option rather than a separate node -- they are the same clock read
#: at a different stride.
PERIODS = ("hourly", "daily", "weekly", "monthly", "months")

#: The ceiling on `every_n`. Two years is already a filing decision rather
#: than a schedule, and a larger number is a typo that would look like a
#: schedule which never fires.
MAX_EVERY_N = 24

DEFAULTS = {
    "enabled": False,
    "every": "daily",
    "every_n": 1,            # months only
    "at": "08:00",           # ignored by hourly
    "minute": 0,             # hourly only: minutes past the hour
    "weekday": 0,            # weekly only, 0 = Monday
    "day": 1,                # monthly/months only, 0 = the last day
    # WHERE it goes. Two channels rather than one, chosen independently:
    # an operator who wants the record in their inbox AND on the shift
    # group's phone should not have to pick. Either alone is normal too, so
    # both are booleans rather than one "channel" selector.
    "send_email": True,
    "send_telegram": False,
    "to": "",
    "account": "",
    "chat_id": "",
    "bot": "",
    "include_clips": False,
    "as_zip": True,
    "subject": "",
    "body": "",
    "last_run": 0.0,         # epoch of the period END last sent for
    "last_result": "",       # what happened, for the page to show
}


def _name(slug):
    return f"footage_export_{slug}"


def load(slug, config_dir=None):
    return {**DEFAULTS, **config.load_local(_name(slug), config_dir)}


def save(data, slug, config_dir=None):
    """Merge a cleaned schedule onto the stored one.

    Everything is clamped rather than trusted: this arrives from a browser and
    then runs unattended for months, so "the UI would never send that" is not
    a check. The run marks are NOT accepted from a caller -- they are the
    record of what actually happened, and a client that could rewrite them
    could make an export send twice or never.
    """
    current = load(slug, config_dir)
    out = dict(current)

    out["enabled"] = bool(data.get("enabled", current["enabled"]))
    every = str(data.get("every") or current["every"]).strip().lower()
    out["every"] = every if every in PERIODS else "daily"
    try:
        out["every_n"] = max(1, min(MAX_EVERY_N, int(data.get("every_n") or 1)))
    except (TypeError, ValueError):
        out["every_n"] = 1
    out["at"] = _clean_hhmm(data.get("at"), current["at"])
    try:
        out["minute"] = max(0, min(59, int(data.get("minute") or 0)))
    except (TypeError, ValueError):
        out["minute"] = 0
    try:
        out["weekday"] = max(0, min(6, int(data.get("weekday") or 0)))
    except (TypeError, ValueError):
        out["weekday"] = 0
    try:
        # 0 = the last day of the month, whatever length it is. A day the
        # month does not have (the 31st of April) runs on its last day rather
        # than skipping that month's report.
        out["day"] = max(0, min(31, int(data.get("day", current["day"]))))
    except (TypeError, ValueError):
        out["day"] = 1
    out["send_email"] = bool(data.get("send_email", current["send_email"]))
    out["send_telegram"] = bool(data.get("send_telegram",
                                         current["send_telegram"]))
    out["to"] = str(data.get("to", current["to"]) or "")[:400]
    out["account"] = str(data.get("account", current["account"]) or "")[:60]
    out["chat_id"] = str(data.get("chat_id", current["chat_id"]) or "")[:60]
    out["bot"] = str(data.get("bot", current["bot"]) or "")[:60]
    out["include_clips"] = bool(data.get("include_clips",
                                         current["include_clips"]))
    out["as_zip"] = bool(data.get("as_zip", current["as_zip"]))
    out["subject"] = str(data.get("subject", current["subject"]) or "")[:200]
    out["body"] = str(data.get("body", current["body"]) or "")[:2000]

    # Turning a schedule ON adopts the current period without sending: saving
    # "weekly, Mondays" on a Wednesday must not immediately post last week.
    if out["enabled"] and not current["enabled"]:
        out["last_run"] = period_end(out, time.time())
        out["last_result"] = ""
    config.save_local(_name(slug), out, config_dir)
    return out


def _clean_hhmm(text, fallback):
    parts = str(text or "").strip().split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError, IndexError):
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return f"{hour:02d}:{minute:02d}"


def period_end(sched, now):
    """Epoch of the END of the most recent period this schedule covers.

    This is the whole scheduling model in one number. An export covers the
    period that CLOSED before it fires, so the answer to "what should have
    gone out by now" is "everything up to this boundary", and whether it has
    is one comparison against `last_run`.

    Deliberately not "now minus 24 hours": a window that slides with the clock
    overlaps its neighbours, so two daily reports cover some of the same hours
    and a gap between them is invisible.
    """
    every = sched.get("every")
    t = datetime.datetime.fromtimestamp(now)
    if every == "hourly":
        minute = int(sched.get("minute") or 0)
        end = t.replace(minute=minute, second=0, microsecond=0)
        if end > t:
            end -= datetime.timedelta(hours=1)
        return end.timestamp()

    hour, minute = [int(x) for x in _clean_hhmm(sched.get("at"), "08:00").split(":")]
    today = t.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if every == "daily":
        return (today if today <= t else today - datetime.timedelta(days=1)).timestamp()

    if every == "weekly":
        want = int(sched.get("weekday") or 0)
        # Back to the most recent `want`-day at the send time, inclusive of
        # today when the time has already passed.
        back = (t.weekday() - want) % 7
        end = today - datetime.timedelta(days=back)
        if end > t:
            end -= datetime.timedelta(days=7)
        return end.timestamp()

    # monthly / every N months: the send day of the most recent month whose
    # send moment has passed.
    end = _month_send(t.year, t.month, sched, hour, minute)
    if end > t:
        year, month = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
        end = _month_send(year, month, sched, hour, minute)
    return end.timestamp()


def _month_send(year, month, sched, hour, minute):
    last = calendar.monthrange(year, month)[1]
    day = int(sched.get("day", 1) or 0)
    # 0 means the last day; a day the month does not have lands on its last,
    # which is what "the 31st" has to mean in February if the report is not
    # simply to vanish eleven times a year.
    day = last if day == 0 else min(day, last)
    return datetime.datetime(year, month, day, hour, minute)


def period_start(sched, end):
    """The START of the period ending at `end` -- the window to collect."""
    every = sched.get("every")
    t = datetime.datetime.fromtimestamp(end)
    if every == "hourly":
        return (t - datetime.timedelta(hours=1)).timestamp()
    if every == "daily":
        return (t - datetime.timedelta(days=1)).timestamp()
    if every == "weekly":
        return (t - datetime.timedelta(days=7)).timestamp()
    step = max(1, int(sched.get("every_n") or 1)) if every == "months" else 1
    year, month = t.year, t.month - step
    while month <= 0:
        month += 12
        year -= 1
    return _month_send(year, month, sched,
                       t.hour, t.minute).timestamp()


def due(sched, now=None):
    """(start, end) of a period that should have gone out and has not, or None.

    `last_run` holds the END of the period last sent for, so a box that was
    off at 08:00 still sends when it comes back -- the boundary it missed is
    still the most recent one -- and a master that restarts mid-morning does
    not send it again.
    """
    if not sched.get("enabled"):
        return None
    # A channel with nowhere to send is not a channel. Both empty means this
    # would fire every period and post nothing, for ever.
    to_mail = sched.get("send_email") and str(sched.get("to") or "").strip()
    to_chat = sched.get("send_telegram") and str(sched.get("chat_id") or "").strip()
    if not (to_mail or to_chat):
        return None
    now = now if now is not None else time.time()
    end = period_end(sched, now)
    if end <= float(sched.get("last_run") or 0) + 1:
        return None
    return period_start(sched, end), end


def mark_ran(slug, end, result, config_dir=None):
    """Record that the period ending at `end` has been dealt with.

    Written whether the send SUCCEEDED or not, and that is deliberate: a
    failing mailbox otherwise retries every minute for ever, and the report
    that matters is the next one rather than another attempt at a period whose
    files are still on the box. What went wrong is stored beside it so the
    page can say so instead of the operator finding out by silence.
    """
    current = load(slug, config_dir)
    current["last_run"] = float(end)
    current["last_result"] = str(result or "")[:300]
    config.save_local(_name(slug), current, config_dir)
    return current


def describe(sched):
    """The schedule in the words the dialog uses, for a page and a log line."""
    every = sched.get("every")
    at = _clean_hhmm(sched.get("at"), "08:00")
    if every == "hourly":
        return f"every hour at {int(sched.get('minute') or 0):02d} past"
    if every == "daily":
        return f"every day at {at}"
    if every == "weekly":
        return f"every {calendar.day_name[int(sched.get('weekday') or 0)]} at {at}"
    day = int(sched.get("day", 1) or 0)
    which = "the last day" if day == 0 else f"day {day}"
    if every == "months":
        n = max(1, int(sched.get("every_n") or 1))
        return f"every {n} months on {which} at {at}"
    return f"every month on {which} at {at}"
