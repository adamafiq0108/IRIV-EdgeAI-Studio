# -*- coding: utf-8 -*-
"""
services/telegram_commands.py -- the box answering Telegram, not just shouting.

Every Telegram feature until now was OUTBOUND: something happened, a message
went out. This is the other direction. Somebody types /summary into a chat, the
bot offers buttons, they tap one, and the answer comes back -- from a machine
they cannot reach, over the one network they always have.

WHY THIS LIVES IN THE MASTER. The detection app has no database; it emits
events and core writes them (the same constraint that shaped the query variable
and the CSV node). A command answers with recorded data, so it is answered by
the process that has the data. The app is not involved at all.

WHAT A COMMAND IS. One node on the canvas. It names the command word, and it
holds a list of buttons; each button has a label, a period, and a Query wired
into its port. Tapping the button runs that statement over that period. The
period is BOUND as %(from)s / %(to)s, never formatted into the text -- a date
pasted into SQL is the one place this could grow an injection, and the person
supplying it is on the far side of the internet.

WHO MAY ASK. An allowlist of chat ids on the bot, and empty means nobody. Not a
default anybody can regret: a bot's @name is discoverable, Telegram has no
login, and a bot that answers whoever finds it is a data leak with a friendly
face. Registration by hand, in Settings, is the deliberate cost.

No framework imports here (docs/conventions.md sec 2).
"""

import datetime
import decimal
import re

#: The periods a button may cover. `custom` asks the person for dates instead
#: of computing them; `none` runs the statement with no bounds at all, for a
#: query that already says what it wants.
PERIODS = ("hour", "last24", "today", "yesterday", "week", "last7", "month",
           "last30", "custom", "none")

#: What each one is called on its own button when the operator typed no label.
PERIOD_LABELS = {
    "hour": "Last hour", "last24": "Last 24 hours",
    "today": "Today", "yesterday": "Yesterday", "week": "This week",
    "last7": "Last 7 days", "month": "This month", "last30": "Last 30 days",
    "custom": "Pick dates", "none": "All time",
}

#: The widest window a PHOTO button may cover. Not a rendering limit -- an
#: album is ten pictures however long the window is -- but the line between two
#: features: a glance at what just happened belongs in a chat, and a week of
#: evidence belongs in Footage > Export, which sends the originals as a zip
#: instead of the recompressed copies Telegram makes of an album.
MAX_ALBUM_HOURS = 24

#: A command word: letters, digits and underscores, which is exactly what
#: Telegram accepts after the slash.
COMMAND_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")

#: How a button answers: the table it always sent, or a picture of the same
#: rows. Named here rather than imported from telegram_chart so this module
#: keeps its promise of importing nothing heavy (matplotlib is not free).
CHART_KINDS = ("table", "bar", "line", "pie")

#: Buttons on one command. Past this the keyboard stops being a menu and
#: becomes a wall -- and Telegram lays them out two per row.
MAX_BUTTONS = 12

#: How many rows an answer may carry into a chat. A phone is not a spreadsheet:
#: past this the table is unreadable and the message is rejected by Telegram
#: anyway (4096 characters).
MAX_ROWS = 30

#: ...and the hard character ceiling Telegram enforces on one message. The
#: table is cut to fit and SAYS it was cut -- a table that stops mid-row
#: without a word reads as a table that ended.
MAX_CHARS = 3500

#: The widest a range may be. Not a performance guard -- the query is capped
#: anyway -- but a person fat-fingering a year gets a refusal that names the
#: limit rather than a timeout that names nothing.
MAX_DAYS = 366


def clean_command(word):
    """A command word without its slash, lowercased, or ""."""
    text = str(word or "").strip().lstrip("/").lower()
    return text if COMMAND_RE.match(text) else ""


def clean_buttons(raw):
    """The button list, validated. Always returns a list, possibly empty."""
    out = []
    for item in (raw if isinstance(raw, (list, tuple)) else [])[:MAX_BUTTONS]:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "today").strip().lower()
        if period not in PERIODS:
            period = "today"
        label = " ".join(str(item.get("label") or "").split())[:32]
        # How the answer is drawn. Unknown -- and everything written before
        # charts existed -- means the table, which is what those buttons have
        # always sent.
        chart = str(item.get("chart") or "table").strip().lower()
        if chart not in CHART_KINDS:
            chart = "table"
        out.append({"label": label or PERIOD_LABELS[period], "period": period,
                    "chart": chart})
    return out


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------

def bounds(period, now=None, custom=None):
    """(from, to) as datetimes for a period, or (None, None) for no bounds.

    Half-open: from <= ts < to, so a day is a day and two adjacent periods
    share no row. `now` is injectable because every one of these is worth
    testing at a boundary rather than at whatever time the suite happens to
    run.
    """
    now = now or datetime.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "none":
        return None, None
    if period == "custom":
        return (custom or (None, None))
    if period == "hour":
        # Rolling, not clock-aligned: somebody asking at 14:05 means the last
        # hour, not the five minutes since 14:00.
        return now - datetime.timedelta(hours=1), now
    if period == "last24":
        return now - datetime.timedelta(hours=24), now
    if period == "today":
        return midnight, midnight + datetime.timedelta(days=1)
    if period == "yesterday":
        return midnight - datetime.timedelta(days=1), midnight
    if period == "week":
        # Monday, because that is what a shift week means everywhere this runs.
        return (midnight - datetime.timedelta(days=midnight.weekday()),
                midnight + datetime.timedelta(days=1))
    if period == "last7":
        return midnight - datetime.timedelta(days=6), midnight + datetime.timedelta(days=1)
    if period == "month":
        return midnight.replace(day=1), midnight + datetime.timedelta(days=1)
    if period == "last30":
        return midnight - datetime.timedelta(days=29), midnight + datetime.timedelta(days=1)
    return None, None


def span_hours(start, stop):
    """How many hours a window covers. An open window is infinite, by design:
    "all time" is exactly the thing a photo button may not ask for."""
    if start is None or stop is None:
        return float("inf")
    return (stop - start).total_seconds() / 3600.0


#: A run long enough to catch every calendar period at its WIDEST. Thirty-one
#: days from the first of a 31-day month covers all seven weekdays and a whole
#: long month, so "this week" is seen on a Sunday and "this month" on the 31st.
#: A fixed date, not today: the answer must not depend on the day the question
#: is asked (see widest_hours).
_PROBE_FROM = datetime.datetime(2026, 1, 1, 12, 0)
_PROBE_DAYS = 31
_widest = {}


def widest_hours(period):
    """The most hours this period can EVER cover, not what it covers today.

    A calendar period is narrow on the day it starts: asked on a Monday,
    "this week" is one day; asked on the 1st, "this month" is one day. A
    button is configured ONCE on the canvas and pressed for months
    afterwards, so judging it by the span it happens to have at compile time
    accepted a "This week" album drawn on a Monday and then overflowed every
    other day of that week. CI found it by running on a Monday.

    Measured by walking the period across a month rather than by listing the
    names that are safe, so a period added later is judged too.
    """
    if period in _widest:
        return _widest[period]
    worst = 0.0
    for day in range(_PROBE_DAYS):
        worst = max(worst, span_hours(*bounds(
            period, _PROBE_FROM + datetime.timedelta(days=day))))
        if worst == float("inf"):
            break
    _widest[period] = worst
    return worst


def album_period_ok(period):
    """May a PHOTO button cover this period?

    Answered from the period NAME where it can be -- so the canvas refuses a
    "This week" album at compile time, with the node to fix in front of the
    person who drew it. `custom` cannot be answered here (the dates arrive
    from a chat months later) and is checked again when they do.

    Judged at the period's widest, never at today's: see widest_hours.
    """
    if period == "custom":
        return True
    return widest_hours(period) <= MAX_ALBUM_HOURS


def parse_dates(text, now=None):
    """(from, to, error) for what somebody typed when asked for dates.

    Deliberately forgiving, because this is typed on a phone by someone who is
    not at a keyboard: one date is that day; two dates are a range, inclusive
    of both, and given in either order. Accepts "to", "-", ".." or a space
    between them, and both 2026-09-01 and 01/09/2026.
    """
    now = now or datetime.datetime.now()
    text = str(text or "").strip()
    # FOUND, not split. Splitting on the separators ate the dashes inside
    # 2026-09-01 and left three numbers, so the commonest way anybody writes a
    # date was the one way this could not read.
    found, spans = [], []
    for m in _DATE_RE.finditer(text):
        day = _one_date(m.group(0))
        if day is not None:
            found.append(day)
            spans.append(m.span())
    # Whatever is left once the dates and the words joining them are taken
    # out. Reported by name: "I did not understand tomorrow" is worth far more
    # than "that is not a date".
    rest = text
    for lo, hi in reversed(spans):
        rest = rest[:lo] + " " + rest[hi:]
    rest = re.sub(r"(?i)\b(?:to|until|and|from)\b|[-,\.]+|\s+", " ", rest).strip()
    if rest:
        return None, None, (f"{rest!r} is not a date I understand - write it "
                            "as 2026-09-01, or two of them for a range.")
    if not found:
        return None, None, ("No date in that. Try 2026-09-01, or "
                            "2026-09-01 to 2026-09-07.")
    if len(found) > 2:
        return None, None, "That is more than two dates - a range takes two."
    start, end = (found[0], found[-1])
    if end < start:
        start, end = end, start
    # Inclusive of the end DAY: somebody asking for the 1st to the 7th means
    # the whole of the 7th, not midnight at the start of it.
    stop = end + datetime.timedelta(days=1)
    if (stop - start).days > MAX_DAYS:
        return None, None, f"That is more than {MAX_DAYS} days - narrow it down."
    if start > now + datetime.timedelta(days=1):
        return None, None, "That is in the future - there is nothing recorded yet."
    return start, stop, ""


#: The shapes a date is written in. Matched WHOLE, so the separators inside one
#: never look like the separator between two.
_DATE_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"          # 2026-09-01, 2026/09/01
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"       # 01/09/2026
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}")    # 1 Sep 2026


def _one_date(token):
    """One date, or None. Accepts ISO and the two common written orders."""
    text = token.strip().strip(".,")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y",
                "%d/%m/%y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def describe(start, stop):
    """A period as a person would say it, for the top of an answer."""
    if start is None or stop is None:
        return "all time"
    last = stop - datetime.timedelta(days=1)
    if start.date() == last.date():
        return start.strftime("%-d %b %Y") if _dash_ok() else start.strftime("%d %b %Y")
    same_year = start.year == last.year
    left = start.strftime("%d %b") if same_year else start.strftime("%d %b %Y")
    return f"{left} – {last.strftime('%d %b %Y')}"


def _dash_ok():
    """%-d is a glibc extension; Windows strftime rejects it. Tested once so a
    developer box does not raise where the Pi would not."""
    try:
        datetime.datetime.now().strftime("%-d")
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# what a chat is shown
# ---------------------------------------------------------------------------

def keyboard(cmd):
    """Telegram's inline_keyboard for one command's buttons, two per row.

    The callback carries the command id and the button index and nothing else:
    a callback is a string a client can forge, so it must name a choice the box
    already published rather than describe one it should make.
    """
    rows, row = [], []
    for i, b in enumerate(cmd.get("buttons") or []):
        row.append({"text": b["label"], "callback_data": f"c:{cmd['id']}:{i}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def welcome(commands, system_name="", custom=""):
    """The /start reply: what this bot is, and what it can be asked.

    Built from the commands that ACTUALLY compiled rather than typed out by
    hand, so a menu can never promise something the canvas no longer has --
    which is what a hand-written welcome does the first time somebody renames
    a command.
    """
    lines = []
    if custom.strip():
        lines.append(custom.strip())
    else:
        who = system_name or "this system"
        lines.append(f"👋 I report on <b>{_esc(who)}</b>.")
    if commands:
        lines.append("")
        lines.append("Here is what you can ask me:")
        for c in commands:
            title = c.get("title") or c["command"]
            lines.append(f"/{c['command']} - {_esc(title)}")
        lines.append("")
        lines.append("Each one opens buttons for the period you want.")
    else:
        lines.append("")
        lines.append("Nothing is set up for me to report yet - a Command node "
                     "on this system's workflow is what gives me something to "
                     "answer.")
    return "\n".join(lines)


def table(columns, rows, title="", period="", note=""):
    """A query result as a message. Plain text in a <pre> block.

    Not Markdown: a column holding an underscore or an asterisk -- a camera
    called `bay_1`, a class called `*` -- turns Markdown into a parse error
    Telegram refuses the whole message for. HTML with three escapes is the
    format that survives real data.
    """
    head = []
    if title:
        head.append(f"<b>{_esc(title)}</b>")
    if period:
        head.append(f"<i>{_esc(period)}</i>")
    if not rows:
        head.append("")
        head.append("Nothing recorded in that period.")
        return "\n".join(head)
    kept = rows[:MAX_ROWS]
    widths = [len(str(c)) for c in columns]
    for r in kept:
        for i, c in enumerate(columns):
            widths[i] = max(widths[i], len(_cell(r.get(c))))
    out = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns)),
           "  ".join("-" * widths[i] for i in range(len(columns)))]
    for r in kept:
        out.append("  ".join(_cell(r.get(c)).ljust(widths[i])
                             for i, c in enumerate(columns)))
    body = "\n".join(out)
    cut = False
    while len(body) > MAX_CHARS and len(out) > 3:
        out.pop()
        body = "\n".join(out)
        cut = True
    head.append(f"<pre>{_esc(body)}</pre>")
    if cut or len(rows) > len(kept):
        shown = len(out) - 2
        head.append(f"<i>Showing the first {shown} of {len(rows)} rows.</i>")
    if note:
        head.append(_esc(note))
    return "\n".join(head)


# ---------------------------------------------------------------------------
# a result as a person would write it (Tok Aba, 2026-10-14)
# ---------------------------------------------------------------------------
# The answer to a button used to be a monospace ASCII table for every result,
# "date  total_violations / 2026-10-14  44" -- correct, and read like a log.
# Now a result is written the way a person would say it: a bold headline, the
# period in italics, the figures as sentences, a small footer naming the
# system. Only a result too wide or too long to read as sentences keeps the
# monospace table, which is still the clearest way to show a grid.
#
# EVERY value is escaped (_esc) before it is wrapped in a tag, and every tag
# is opened and closed on one line by this code, never by data: a cell holding
# "<b>x</b>&" arrives as text, and no result can leave a tag unclosed.

#: The one headline marker. Few and plain, as asked: a report is a report.
HEADLINE_EMOJI = "\U0001F4CA"
#: Sentences for up to this many rows and columns; past either, the table.
NATURAL_ROWS = 12
NATURAL_COLS = 4
#: A text cell longer than this is cut, and says so with "...".
CELL_CHARS = 80
#: Telegram refuses a message past 4096 characters.
MESSAGE_CHARS = 4096

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")


def nice_label(column):
    """A column name as a label: total_violations -> Total violations."""
    words = str(column or "").replace("_", " ").strip()
    words = " ".join(words.split())
    return (words[:1].upper() + words[1:]) if words else "Value"


def _is_number(value):
    return (isinstance(value, (int, float, decimal.Decimal))
            and not isinstance(value, bool))


def _day_text(day):
    text = day.strftime("%a %d %b %Y")
    return text[:4] + text[4:].lstrip("0") if text[4:5] == "0" else text


def nice_value(value):
    """One cell as a person would write it, NOT yet escaped: 1234 -> 1,234,
    2.50 -> 2.5, 2026-10-14 -> Wed 14 Oct 2026, a long text cut with ...,
    nothing -> -."""
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if _is_number(value):
        number = float(value)
        if number.is_integer() and abs(number) < 1e15:
            return f"{int(number):,}"
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime.datetime):
        return f"{_day_text(value)}, {value.strftime('%H:%M:%S')}"
    if isinstance(value, datetime.date):
        return _day_text(value)
    text = " ".join(str(value).split())
    try:
        if _ISO_DAY.match(text):
            return _day_text(datetime.date.fromisoformat(text))
        if _ISO_TIME.match(text):
            moment = datetime.datetime.fromisoformat(text[:19].replace(" ", "T"))
            return f"{_day_text(moment)}, {moment.strftime('%H:%M:%S')}"
    except ValueError:
        pass
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 3] + "..."


def period_line(label, start, stop):
    """"Today, Wed 14 Oct 2026": the button's own word, then the dates."""
    if start is not None and stop is not None \
            and (stop - start) <= datetime.timedelta(days=1) \
            and start.time() == datetime.time(0, 0):
        when = _day_text(start)
    else:
        when = describe(start, stop)
    if label and label.lower() == when.lower():
        return label
    return f"{label}, {when}" if label else when


def report(columns, rows, title="", period="", footer=""):
    """A query result as a Telegram HTML message that reads naturally.

      headline   the title in bold, after one plain emoji
      period     in italics ("Today, Wed 14 Oct 2026")
      one row    a "Label: <b>value</b>" line per column
      few rows   one line per row: the first column, then the others, the
                 numbers in bold (named when there are one or two of them),
                 and a total when exactly one column is a number
      many       the monospace table (table()), cut to fit and saying so
      none       "No records for this period." in italics
      footer     in italics: the system and the period's name
    """
    columns = [str(c) for c in columns or []]
    rows = list(rows or [])
    head = []
    if title:
        head.append(f"{HEADLINE_EMOJI} <b>{_esc(title)}</b>")
    if period:
        head.append(f"<i>{_esc(period)}</i>")
    tail = [f"<i>{_esc(footer)}</i>"] if footer else []
    if not rows:
        body = ["<i>No records for this period.</i>"]
    elif len(rows) > NATURAL_ROWS or len(columns) > NATURAL_COLS:
        body = [table(columns, rows)]
    elif len(rows) == 1:
        body = [f"{_esc(nice_label(c))}: <b>{_esc(nice_value(rows[0].get(c)))}</b>"
                for c in columns]
    else:
        body = _rows_as_lines(columns, rows)
    text = "\n".join(head + ([""] if head else []) + body + ([""] if tail else []) + tail)
    if len(text) > MESSAGE_CHARS:
        # Sentences that will not fit are a table that will.
        text = "\n".join(head + ([""] if head else []) + [table(columns, rows)]
                         + ([""] if tail else []) + tail)
    if len(text) > MESSAGE_CHARS:
        text = table(columns, rows, title=title, period=period)
    return text


def _rows_as_lines(columns, rows):
    label, others = columns[0], columns[1:]
    numeric = [c for c in others
               if any(r.get(c) is not None for r in rows)
               and all(r.get(c) is None or _is_number(r.get(c)) for r in rows)]
    named = 0 < len(numeric) <= 2
    lines = []
    for r in rows:
        parts = [_esc(nice_value(r.get(label)))]
        for c in others:
            value = _esc(nice_value(r.get(c)))
            if c in numeric:
                parts.append(f"<b>{value}</b> {_esc(nice_label(c).lower())}" if named
                             else f"<b>{value}</b>")
            else:
                parts.append(value)
        lines.append(" · ".join(parts))
    if len(numeric) == 1:
        total = sum(float(r.get(numeric[0]) or 0) for r in rows)
        lines.append("")
        lines.append(f"Total: <b>{_esc(nice_value(total))}</b>")
    return lines


def plain(html_text):
    """The same message with its tags taken out and its escapes undone: what
    is sent when Telegram refuses the HTML (send() retries once like this)."""
    text = re.sub(r"</?(b|i|u|s|code|pre)>", "", str(html_text or ""))
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)[:40]


def _esc(text):
    """The three characters Telegram's HTML mode reserves."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
