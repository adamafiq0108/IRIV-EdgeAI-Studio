# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
services/alert_watch.py -- a number in the database crossing a line.

The box already alerts on what a camera is seeing THIS INSTANT: an analytics
node, a Condition, a Telegram node, and the message leaves within the frame.
That path is the right one for "there are too many people in the zone".

This is the other question, and it cannot be answered the same way: "more than
fifty violations today" is about RECORDED data, and the detection app has no
database -- it emits events and core writes them. So a Watch is run by the
master, on a timer, over the same bound period a command button uses, and it
sends through the same Telegram or Email node the operator already knows.

WHY IT DOES NOT SEND EVERY TIME IT LOOKS. A query stuck at fifty-one is over
the line at every single check. Sending on each one would mail somebody every
fifteen minutes until the day rolled over, which is how an alert becomes a
filter rule in a mail client. A Watch fires on the CROSSING -- it was under,
now it is over -- and then stays quiet for a set time even if it crosses again.

No framework imports here (docs/conventions.md sec 2).
"""

import re

#: The tests a Watch may make. Deliberately small: this compares one number to
#: one number, and anything richer belongs in the SQL, where it can be read.
OPS = (">", ">=", "<", "<=", "==", "!=")

#: Ceilings. A watch runs a real query on a box doing inference, so the floor
#: on `every_min` is what stops a canvas from asking for a query per second.
MAX_WATCHES = 20
MIN_EVERY_MIN = 1
MAX_EVERY_MIN = 1440            # a day
MAX_QUIET_MIN = 1440

#: What a message may quote. Filled in when the watch fires; unknown names are
#: left alone rather than blanked, so a stray brace in somebody's prose is not
#: silently eaten.
TOKENS = ("value", "threshold", "name", "period", "table")


def clean_watch(d):
    """One Watch node's settings, normalized. Never raises."""
    d = d if isinstance(d, dict) else {}
    op = str(d.get("op") or ">").strip()
    period = str(d.get("period") or "today").strip().lower()
    return {
        "name": " ".join(str(d.get("name") or "").split())[:60] or "Watch",
        "every_min": _clamp(d.get("every_min"), MIN_EVERY_MIN, MAX_EVERY_MIN, 15),
        "period": period,
        "op": op if op in OPS else ">",
        "value": _float(d.get("value"), 0.0),
        "quiet_min": _clamp(d.get("quiet_min"), 0, MAX_QUIET_MIN, 60),
    }


def _clamp(raw, low, high, fallback):
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, n))


def _float(raw, fallback=0.0):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def test(op, value, threshold):
    """Is this number over the line? Unknown op is False, never an exception --
    a stored watch is read on a timer, and one bad row must not stop the rest."""
    if value is None:
        return False
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    return False


def pick_value(columns, rows):
    """The number a Watch tests: the FIRST numeric cell of the FIRST row.

    One rule, said out loud in the inspector, rather than a guess that changes
    with the shape of the result. "SELECT COUNT(*) ..." is the statement this
    is for, and it is already exactly one row and one column.
    """
    for row in (rows or [])[:1]:
        for col in (columns or []):
            n = _cell_number(row.get(col))
            if n is not None:
                return n
    return None


def _cell_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


_TOKEN_RE = re.compile(r"\{(" + "|".join(TOKENS) + r")\}")


def render(text, values):
    """Expand {value}, {threshold}, {name}, {period}, {table} in a message."""
    def swap(m):
        got = values.get(m.group(1))
        return "" if got is None else str(got)
    return _TOKEN_RE.sub(swap, str(text or ""))


def number(value):
    """A number as a person would write it: 51, not 51.0."""
    if value is None:
        return "-"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def should_send(state, over, now_ts, quiet_min):
    """(send?, new_state) for one check.

    RISING EDGE plus a quiet time. `state` is the caller's dict for this watch
    and carries {"over": bool, "sent": float}; a watch never seen before has
    neither, and its first check sends if it is already over -- which is what
    somebody who just wired it expects to see.
    """
    was = bool(state.get("over"))
    sent = float(state.get("sent") or 0)
    new = {"over": bool(over), "sent": sent}
    if not over:
        return False, new                       # back under: re-arm, say nothing
    if was:
        return False, new                       # still over: already reported
    if quiet_min and sent and (now_ts - sent) < quiet_min * 60:
        # Crossed again inside the quiet time. Deliberately NOT re-armed --
        # a value flapping across the line is one situation, not twenty.
        return False, new
    new["sent"] = now_ts
    return True, new


def commit(state, new_state, delivered=True):
    """Fold one check's result into the watch's state, AFTER the sends ran.

    The rising edge is spent only when a message actually LEFT the box. The
    state used to be written the moment the decision was made, before the
    first send was attempted, so a refused SMTP login or a Telegram timeout
    marked the watch as already reported -- and a number that stays over the
    line never alerts again, because there is no second crossing while it
    never goes back under. One transient failure silenced the watch for good
    and nothing on the screen said so.

    `delivered` is "at least one channel took it", not "all of them did". A
    watch with a working chat and a broken mailbox has already told somebody;
    re-arming it on the mailbox's account would re-alarm that chat at every
    check for as long as the mailbox stays broken. The channel that failed
    goes to the log, which is where half a delivery belongs.

    Nothing is rolled back on failure -- the state is simply not advanced --
    so the quiet time from the last real send survives the failed attempt and
    a flapping value still cannot turn into twenty messages.
    """
    if delivered:
        state.update(new_state)
    return state
