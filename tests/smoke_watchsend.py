# One failed send used to silence a Watch for good.
#
# _run_watch committed the rising-edge state BEFORE it tried to send, so a
# refused SMTP login, a Telegram timeout, a minute of no route to the internet
# -- any of them -- left the watch marked "already reported". A Watch fires on
# the CROSSING, and there is no second crossing while the number stays over the
# line, so the alert never came again: the condition that mattered most (still
# over, hours later) was the one guaranteed never to be reported.
#
# What this pins:
#
#   * a failed send does not spend the edge -- the next check, still over,
#     sends again;
#   * a send that works does spend it, so a stuck number is not mailed every
#     fifteen minutes;
#   * PARTIAL success counts as sent: a watch with a working chat and a broken
#     mailbox must not re-alarm the chat for ever;
#   * the quiet time survives a failed attempt, so a value flapping across the
#     line is still one situation and not twenty;
#   * the master reports those failures through the logger, which is what the
#     Logs page reads -- a print() in that process goes to a console nobody on
#     the box is watching.
#
#     python tests/smoke_watchsend.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import alert_watch as aw                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def evaluate(st, over, now, quiet_min, channels):
    """One check of one watch, exactly as _run_watch performs it.

    `channels` is what each send node does this time round: True delivered,
    False raised or was refused. Returns whether a message was attempted.
    """
    send_it, new_state = aw.should_send(st, over, now, quiet_min)
    if not send_it:
        aw.commit(st, new_state)
        return False
    delivered = not channels
    for ok in channels:
        delivered = delivered or ok
    aw.commit(st, new_state, delivered)
    return True


# -- the bug ------------------------------------------------------------------
# Value goes over the line. The only send node throws. The condition is still
# over at the next check -- and that check has to try again.
st = {}
first = evaluate(st, True, 1000.0, 0, [False])
second = evaluate(st, True, 2000.0, 0, [False])
check("a failed send leaves the watch able to fire again while still over",
      first and second, f"first={first} second={second} state={st}")

# ...and it keeps trying, rather than trying exactly twice.
check("it keeps trying for as long as the send keeps failing",
      evaluate(st, True, 3000.0, 0, [False]), str(st))

# -- and the thing the bug was protecting --------------------------------------
# A send that WORKS still spends the edge: a number stuck at fifty-one must not
# be mailed at every single check, which is how an alert becomes a filter rule.
st = {}
check("a successful send is reported once", evaluate(st, True, 1000.0, 0, [True]))
check("...and not again while it stays over",
      not evaluate(st, True, 2000.0, 0, [True]), str(st))

# Back under and over again is a new situation, and is reported.
evaluate(st, False, 3000.0, 0, [True])
check("a fresh crossing is a fresh alert", evaluate(st, True, 4000.0, 0, [True]))

# -- partial success -----------------------------------------------------------
# Two channels, one working. The message reached somebody, so the edge is
# spent -- re-arming on the broken mailbox's account would re-alarm the
# working chat at every check for as long as the mailbox stays broken.
st = {}
check("a half-delivered alert is delivered", evaluate(st, True, 1000.0, 0, [True, False]))
check("...and does not re-alarm the channel that worked",
      not evaluate(st, True, 2000.0, 0, [True, False]), str(st))

# -- a watch with nowhere to send ----------------------------------------------
# Nothing can fail, so nothing is retried: this is not a delivery failure, it
# is a watch nobody wired an output to.
st = {}
check("a watch with no send nodes still settles",
      evaluate(st, True, 1000.0, 0, [])
      and not evaluate(st, True, 2000.0, 0, []), str(st))

# -- the quiet time survives the failure ---------------------------------------
# Nothing is rolled BACK on a failure, the state is simply not advanced, so the
# last real send's timestamp is still there and a flapping value cannot turn
# into twenty messages.
st = {}
evaluate(st, True, 1000.0, 60, [True])            # sent, quiet for an hour
sent_at = st.get("sent")
evaluate(st, False, 1100.0, 60, [True])           # back under, re-armed
attempted = evaluate(st, True, 1200.0, 60, [False])
check("a failed send does not reset the quiet time",
      not attempted and st.get("sent") == sent_at, str(st))

# -- the master does it in that order -------------------------------------------
master = read("master_server.py")
watch = master.split("def _run_watch")[1].split("def _watch_telegram")[0]
check("the master commits the watch's state only after the sends were tried",
      watch.index("for send in w[\"sends\"]") < watch.index("aw.commit(st, new_state, delivered)"),
      watch)
check("...and never writes it straight from should_send",
      "st.update(new_state)" not in watch, watch)

# -- and says so where the box can be read --------------------------------------
# The log ring is installed in this process (master_server.py, logs_service
# .install()), so a print() here is a line on a console nobody on the board is
# looking at while the Logs page shows nothing wrong at all.
watch_path = (master.split("async def _watch_loop")[1]
                    .split("@contextlib.asynccontextmanager")[0])
check("nothing on the watch path is left on print()",
      "print(" not in watch_path,
      [ln.strip() for ln in watch_path.splitlines() if "print(" in ln])
check("the watch failures reach the Logs page",
      watch_path.count('logger.warning("[WATCH]') >= 5
      and 'logger.exception("[WATCH]' in watch_path)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
