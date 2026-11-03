# -*- coding: utf-8 -*-
"""
services/telegram_chart.py -- a query result as a picture.

A table of thirty rows on a phone is a wall of digits; the same thirty rows as
a line is a shape somebody reads while walking. So a command button says how it
wants to be answered -- table, bar, line or pie -- and the SAME statement over
the SAME bound period renders either way. Nothing about the query changes.

Matplotlib is already a dependency (services/email.py renders the daily report
with it), so this adds a use, not a package. Agg is forced before pyplot is
imported: the box has no display, and pyplot picking an interactive backend on
a headless machine is an exception rather than a warning.

EVERY failure here returns None rather than raising. A chart that cannot be
drawn must fall back to the table it was an alternative to -- an operator who
picked "bar" on a query with no numbers should get their numbers, not silence.
"""

import datetime
import io

#: How a button may be answered. `table` is the original behaviour and stays
#: the default, so every command written before this existed is unchanged.
KINDS = ("table", "bar", "line", "pie")

#: Slices past this on a pie are unreadable; the rest are summed into "other".
MAX_SLICES = 8

#: Bars/points past this crowd the axis into a smear. The newest are kept --
#: a report is nearly always ordered oldest first, and the recent end is the
#: end anybody asked about.
MAX_POINTS = 40


def render(columns, rows, kind, title="", subtitle=""):
    """PNG bytes for one result, or None if it cannot be drawn as `kind`.

    Reads the FIRST column as the labels and the first numeric column after it
    as the values, which is what "SELECT date, COUNT(*) ... GROUP BY date"
    already produces. Guessing beyond that would mean a chart that quietly
    plots a different column than the one an operator saw in the table.
    """
    kind = str(kind or "table").lower()
    if kind not in KINDS or kind == "table":
        return None
    columns = list(columns or [])
    rows = list(rows or [])
    if len(columns) < 2 or not rows:
        return None
    label_col = columns[0]
    value_col = _numeric_column(columns[1:], rows)
    if value_col is None:
        return None

    labels, values = [], []
    for r in rows[-MAX_POINTS:]:
        value = _number(r.get(value_col))
        if value is None:
            continue
        labels.append(_label(r.get(label_col)))
        values.append(value)
    if not values:
        return None
    if kind == "pie":
        labels, values = _top_slices(labels, values)
        if not values or sum(values) <= 0:
            return None

    try:
        return _draw(kind, labels, values, value_col, title, subtitle)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] chart not drawn: {exc}")
        return None


def _draw(kind, labels, values, value_col, title, subtitle):
    import matplotlib                                         # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt                           # noqa: PLC0415

    # Sized for a phone held upright: Telegram scales a photo to the chat
    # width, so a wide figure becomes small text.
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=110)
    colour = "#2563EB"
    if kind == "bar":
        ax.bar(range(len(values)), values, color=colour)
        _category_axis(ax, labels)
        ax.set_ylabel(str(value_col))
    elif kind == "line":
        ax.plot(range(len(values)), values, color=colour, marker="o",
                markersize=3.5, linewidth=1.8)
        _category_axis(ax, labels)
        ax.set_ylabel(str(value_col))
        ax.grid(True, axis="y", alpha=0.25)
    else:                                                     # pie
        ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90,
               counterclock=False, textprops={"fontsize": 9})
        ax.axis("equal")
    head = " - ".join(x for x in (title, subtitle) if x)
    if head:
        ax.set_title(head, fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _category_axis(ax, labels):
    """Ticks that stay readable however many there are: every label while they
    fit, thinned after that, and turned when they are long."""
    step = max(1, len(labels) // 12 + 1)
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks)
    longest = max((len(labels[i]) for i in ticks), default=0)
    ax.set_xticklabels([labels[i] for i in ticks], fontsize=8,
                       rotation=45 if longest > 4 else 0,
                       ha="right" if longest > 4 else "center")


def _numeric_column(candidates, rows):
    """The first column that actually holds numbers, or None. Checked against
    the DATA rather than a declared type: the console hands back whatever the
    driver produced, and a count can arrive as a string."""
    for col in candidates:
        if any(_number(r.get(col)) is not None for r in rows):
            return col
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _label(value):
    """A short axis label. Dates lose the year, which every point shares."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%d %b")
    text = str(value if value is not None else "-")
    return text[:18]


def _top_slices(labels, values):
    """The biggest slices, with the tail summed into one. A pie with forty
    slivers says less than a pie with eight and an "other"."""
    pairs = sorted(zip(labels, values), key=lambda p: p[1], reverse=True)
    pairs = [(l, v) for l, v in pairs if v > 0]
    if len(pairs) <= MAX_SLICES:
        return [p[0] for p in pairs], [p[1] for p in pairs]
    kept = pairs[:MAX_SLICES - 1]
    rest = sum(v for _l, v in pairs[MAX_SLICES - 1:])
    return ([l for l, _v in kept] + ["other"],
            [v for _l, v in kept] + [rest])
