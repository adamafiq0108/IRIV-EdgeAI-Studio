# "Did it actually send?" -- the question an output could not answer.
#
# The operator's words: "ensure that all nodes that got output can be debug,
# including the board/telegram/email/ because just to check is it send or not,
# the output function or not on board or gpio".
#
# Nothing in outputs.py wrote down an output's RESULT. _fire_one dispatched to
# gpio / mqtt / webhook / email / telegram / the board ports and kept nothing
# at all, so a Telegram that came back 401, a relay whose port was held by
# another process and a webhook to a dead host looked identical from every
# page: the canvas drew the rule going true and stopped there. The only
# signal was a line in app.log -- the one place somebody standing at a
# dashboard cannot read.
#
# Three things make this hard to see and are each pinned below:
#
#   * the three network senders (email, telegram, webhook) work on their OWN
#     thread and catch the failure there, so the caller learns nothing and a
#     naive "it returned, so it sent" would report every one of them as fine;
#   * a DELAYED send fails after the detection loop has moved on -- a Telegram
#     with a 30s delay is exactly the case nobody could see;
#   * the message describing the failure can quote a credential (a webhook URL
#     with a password, a bot token), and this one is published to every
#     dashboard rather than written to a file.
#
#     py -3 tests/smoke_outputstat.py
import os
import sys
import tempfile
import time
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

# The canvas route is asserted at the end and it imports fastapi, which is not
# installed on every dev box (same trick as tests/smoke_dataset_query.py).
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), put=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    sys.modules["fastapi"] = fa

from app import outputs as out                               # noqa: E402
from core.routes import workflow as wf_routes                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


EVENT = {"type": "alert", "detail": {}}


def keys(oid, outputs=None):
    t = out.telemetry(outputs)
    return (t.get(f"out_{oid}_fired"), t.get(f"out_{oid}_ok"),
            t.get(f"out_{oid}_at"), t.get(f"out_{oid}_error"))


# -- 1. a fire that worked ----------------------------------------------------
ok_out = {"id": "snap1", "type": "snapshot", "trigger": "alert",
          "enabled": True}
out.fire([ok_out], EVENT, hooks={"snapshot": lambda o, ev=None: None})
fired, good, at, err = keys("snap1")
check("a fire that worked is counted", fired == 1, fired)
check("...and reads ok", good == 1, good)
check("...and stamps when", at and at > 0, at)
check("...and carries no error text", err == "", repr(err))

out.fire([ok_out], EVENT, hooks={"snapshot": lambda o, ev=None: None})
check("firing again counts again", keys("snap1")[0] == 2, keys("snap1"))


# -- 2. a fire that failed ----------------------------------------------------
def boom(o, ev=None):
    raise RuntimeError("DO0: port is held by another process")


bad_out = {"id": "relay1", "type": "snapshot", "trigger": "alert",
           "enabled": True}
out.fire([bad_out], EVENT, hooks={"snapshot": boom})
fired, good, at, err = keys("relay1")
# A failed attempt COUNTS. "Tried once, it failed" is the answer; a counter
# that only moved on success would read 0, which is what a never-wired output
# reads like -- the exact confusion this exists to end.
check("a fire that failed is counted too", fired == 1, fired)
check("...and reads not-ok", good == 0, good)
check("...and says what went wrong",
      err == "DO0: port is held by another process", repr(err))
check("...and stamps when it was tried", at and at > 0, at)

# The failure is still fatal exactly as far as it was before, and still
# logged: fire() catches it per output, so the NEXT output still runs.
seen = []
out.fire([bad_out, {"id": "snap2", "type": "snapshot", "trigger": "alert",
                    "enabled": True}],
         EVENT, hooks={"snapshot": lambda o, ev=None: (boom(o) if o["id"] ==
                                                       "relay1" else
                                                       seen.append(o["id"]))})
check("one output's failure still does not stop the next",
      seen == ["snap2"] and keys("snap2")[1] == 1, (seen, keys("snap2")))


# -- 3. an output that has never fired ----------------------------------------
never = {"id": "tg_night", "type": "telegram", "trigger": "violation",
         "enabled": True}
fired, good, at, err = keys("tg_night", [never])
check("a never-fired output still publishes its four keys",
      (fired, good, at, err) == (0, 0, 0.0, ""), (fired, good, at, err))
check("...because 'never' and 'the page did not load' must not look alike",
      f"out_tg_night_at" in out.telemetry([never]))


# -- 4. a DELAYED send that fails after the wait ------------------------------
delayed = {"id": "tg_late", "type": "snapshot", "trigger": "alert",
           "enabled": True, "delay_s": 0.3}
started = time.time()
out.fire([delayed], EVENT, hooks={"snapshot": boom})
check("a delayed output has not fired when fire() returns",
      keys("tg_late", [delayed])[0] == 0, keys("tg_late", [delayed]))
# Against the delay itself, not a fixed 0.1s: blocking would cost the full
# 0.3s, and a loaded machine can take 0.1s just to start the timer.
check("...and fire() did not block the detection loop",
      time.time() - started < delayed["delay_s"], time.time() - started)
for _ in range(250):                    # up to 5s for the delayed send
    if keys("tg_late")[0]:
        break
    time.sleep(0.02)
fired, good, at, err = keys("tg_late")
check("a delayed send that fails 300ms later is still recorded", fired == 1,
      fired)
check("...as a failure", good == 0 and err, (good, err))


# -- 5. a credential never becomes a dashboard value --------------------------
def leak(o, ev=None):
    raise RuntimeError("POST https://bob:hunter2@hooks.example.com/alert "
                       "failed; bot_token=8112233:AAHxyz-secret rejected")


out.fire([{"id": "hook1", "type": "snapshot", "trigger": "alert",
           "enabled": True}], EVENT, hooks={"snapshot": leak})
err = keys("hook1")[3]
check("a password in an error message is masked", "hunter2" not in err, err)
check("...and a bot token with it", "AAHxyz-secret" not in err, err)
check("...while the message still says what happened",
      "hooks.example.com" in err, err)


# -- 6. a sender that works on its own thread reports from there --------------
# The whole class of failure the operator was asking about: _webhook, _email
# and _telegram return the moment the thread starts, and catch the failure
# inside it. Counting the caller's return would report every one of them as a
# success. urlopen is replaced rather than a port used, so this test touches
# no network at all.
real_urlopen = urllib.request.urlopen


def refuse(*a, **k):
    raise OSError("[Errno 111] Connection refused")


urllib.request.urlopen = refuse
# A webhook sends through netsafe.request (Pipi M-R1: checked address, no
# redirects), so that is what refuses here.
real_request = out.netsafe.request
out.netsafe.request = refuse
try:
    out._webhook({"id": "wh1", "type": "webhook",
                  "url": "https://hooks.example.com/alert"})
    for _ in range(50):
        if keys("wh1")[0]:
            break
        time.sleep(0.02)
finally:
    urllib.request.urlopen = real_urlopen
    out.netsafe.request = real_request
fired, good, at, err = keys("wh1")
check("a webhook that failed on its own thread is recorded", fired == 1, fired)
check("...as a failure, not as the send that 'returned fine'",
      good == 0 and "refused" in err, (good, err))
check("...exactly once -- the caller does not count it as well", fired == 1,
      fired)


# -- 7. an output that DECIDED to do nothing is not a fire --------------------
paced = {"id": "cap_paced", "type": "snapshot", "trigger": "alert",
         "enabled": True, "mode": "interval", "interval_s": 60}
out.fire([paced], EVENT, hooks={"snapshot": lambda o, ev=None: None})
out.fire([paced], EVENT, hooks={"snapshot": lambda o, ev=None: None})
check("a capture paced out does not count as a second fire",
      keys("cap_paced")[0] == 1, keys("cap_paced"))
out.fire([{"id": "nohook", "type": "snapshot", "trigger": "alert",
           "enabled": True}], EVENT, hooks={})
check("a dispatch with nothing wired to perform it counts nothing",
      keys("nohook", [{"id": "nohook"}])[0] == 0, keys("nohook"))


# -- 8. the canvas can read them ----------------------------------------------
# core/routes/workflow.py filters the live values by key PREFIX, so a whole
# family of keys is invisible until the prefix is listed.
live = wf_routes._live_values({"out_snap1_fired": 2, "out_snap1_error": "",
                               "fn_a_count": 1, "camera_label": "cam0"})
check("out_ keys reach the canvas", live.get("out_snap1_fired") == 2, live)
check("...including the text one, which is why a text stat had to land first",
      "out_snap1_error" in live, live)
check("...and nothing else came with them", "camera_label" not in live, live)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
