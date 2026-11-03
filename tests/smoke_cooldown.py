# An alert's cooldown, against a condition that FLICKERS.
#
# The cooldown used to gate only the repeats: a rising edge fired whatever the
# clock said. That is fine for a condition that switches once and holds, and
# useless for every real one -- a zone count bouncing 1-2-1-2 with detector
# jitter is a fresh rising edge every other frame, so a rule set to one alert
# every 30 seconds sent 46 in a minute on a real box (192.168.1.234,
# cycle_time_6la1, cooldown_s = 30). The operator sees a flood from a rule they
# explicitly paced, and no setting they can reach makes it stop.
#
# So the cooldown gates the edge too. Everything else about triggering is
# unchanged, and this asserts that as hard as it asserts the fix: the first
# alert is still immediate, a held condition still repeats on its own pace, and
# a cooldown of zero still means every edge.
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "systems" / "demo"))

from app.functions import Function, FunctionEngine                # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def rule(cooldown_s, **trig):
    t = {"metric": "count", "op": ">", "value": 1, "severity": "crit",
         "message": "too many", "cooldown_s": cooldown_s}
    t.update(trig)
    return Function({"id": "f1", "type": "zone_count", "name": "z",
                     "params": {"zone": "z1"}, "triggers": [t]})


def engine(fn):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats = [fn], {}, {}
    return e


def run(e, frames):
    """[(t, value)] -> the times it fired."""
    out = []
    for t, value in frames:
        e._stats["fn_f1_count"] = value
        if e._check_triggers(t):
            out.append(t)
    return out


print("== a condition that holds")
e = engine(rule(30))
fired = run(e, [(t, 2) for t in range(0, 91, 1)])
check("the first alert is immediate", fired and fired[0] == 0, str(fired[:3]))
check("then one per cooldown, not one per frame",
      fired == [0, 30, 60, 90], str(fired))

print("== a condition that flickers -- the bug")
# 1-2-1-2 at 2 Hz for a minute: 60 rising edges, one alert.
e = engine(rule(30))
frames = [(t / 2.0, 2 if int(t) % 2 == 0 else 1) for t in range(0, 120)]
fired = run(e, frames)
check("a flickering condition cannot outrun its cooldown",
      len(fired) == 2, "%d alerts: %s" % (len(fired), fired[:6]))
check("and the ones it does send are a cooldown apart",
      fired == [0.0, 30.0], str(fired))

print("== the cooldown still lets go")
e = engine(rule(10))
fired = run(e, [(0.0, 2), (1.0, 1), (2.0, 2), (5.0, 1),
                (10.5, 2), (11.0, 1), (12.0, 2)])
check("a rise after the cooldown fires", fired == [0.0, 10.5], str(fired))

print("== a cooldown of zero still means every edge")
e = engine(rule(0))
fired = run(e, [(0.0, 2), (0.1, 1), (0.2, 2), (0.3, 1), (0.4, 2)])
check("every rising edge fires", fired == [0.0, 0.2, 0.4], str(fired))
e = engine(rule(0))
fired = run(e, [(t / 10.0, 2) for t in range(0, 20)])
check("but a held condition does not repeat", fired == [0.0], str(fired))

print("== an edge-mode trigger is paced the same way")
# `changed` is true on any frame whose value differs from the last -- by
# construction a single-frame condition, so before the fix its cooldown did
# nothing at all.
e = engine(rule(30, mode="changed"))
fired = run(e, [(float(t), t % 2) for t in range(0, 90)])
check("a per-frame edge mode obeys its cooldown",
      fired == [1.0, 31.0, 61.0], str(fired))

print("== the value carried is the one that tripped it")
e = engine(rule(30))
e._stats["fn_f1_count"] = 5
ev = e._check_triggers(0.0)
check("the event carries the reading", ev and ev[0]["value"] == 5.0, str(ev))
check("and the rule's own message",
      ev and ev[0]["message"] == "too many" and ev[0]["severity"] == "crit",
      str(ev))
check("`active` follows the condition, not the cooldown",
      e._stats.get("fn_f1_active") == 1)
e._stats["fn_f1_count"] = 0
e._check_triggers(1.0)
check("and clears when it stops holding", e._stats.get("fn_f1_active") == 0)

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
