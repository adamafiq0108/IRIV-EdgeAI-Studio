# The app's per-frame state is paced to disk (perf audit item L1, 2026-10-02).
#
# run.py used to rewrite state.json on EVERY frame -- read, merge, write a
# temp file, rename -- 15 times a second per system at 15 fps, on an SD card.
# runtime.heartbeat() keeps it in memory and writes about once a second, and
# AT ONCE when running / pid / error / the AI dot changes. Pinned here: the
# pacing, the immediate writes, that the staleness thresholds the pages use
# still hold on a healthy loop, that --once still writes, and that the write
# itself is still the atomic temp-file-and-rename.
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


from app import runtime                                             # noqa: E402

clock = [1000.0]
runtime.time = type("T", (), {"time": staticmethod(lambda: clock[0])})
writes = []
real_write = runtime.write


def counting_write(data_dir, **fields):
    writes.append(dict(fields))
    return real_write(data_dir, **fields)


runtime.write = counting_write
d = tempfile.mkdtemp()


def frame(**over):
    base = dict(running=True, pid=4242, mode="unified", ai_on=True, ai_ok=True,
                ai_error=None, error=None, counts={"n": clock[0]}, fps=15.0)
    base.update(over)
    return runtime.heartbeat(d, **base)


# 10 seconds of a healthy loop at 15 fps
for i in range(150):
    frame()
    clock[0] += 1 / 15
before, after = 150, len(writes)
check(f"15 fps for 10 s: {after} writes instead of {before}",
      10 <= after <= 11, after)
state = json.load(open(os.path.join(d, "state.json")))
check("what is on disk is the newest frame",
      abs(state["counts"]["n"] - (clock[0] - 1 / 15)) < 1.0, state["counts"])
check("the heartbeat on disk is never older than FLUSH_EVERY_S on a healthy loop",
      clock[0] - state["heartbeat"] <= runtime.FLUSH_EVERY_S + 1 / 15)
check("...well inside AI_OK_FRESH_S and STALE_AFTER_S",
      runtime.FLUSH_EVERY_S * 3 < runtime.AI_OK_FRESH_S < runtime.STALE_AFTER_S)

n = len(writes)
frame(ai_ok=False, ai_error="no frames from camera cam1")
check("the AI dot going red is written at once", len(writes) == n + 1
      and writes[-1]["ai_ok"] is False)
frame(ai_ok=False, ai_error="no frames from camera cam1")
check("...and not again while it stays red", len(writes) == n + 1)
frame(error="camera cam1 lost")
check("a new error is written at once, stamped",
      len(writes) == n + 2
      and json.load(open(os.path.join(d, "state.json")))["error_at"] == clock[0])
frame(running=False)
check("running changing is written at once", len(writes) == n + 3)
frame(pid=999)
check("a new pid is written at once", len(writes) == n + 4)
check("--once forces its single frame out", frame(pid=999, force=True) is True)

runtime.write = real_write
check("the write itself is still temp file + atomic rename",
      'os.replace(tmp, _path(data_dir))' in io.open(
          os.path.join(ROOT, "systems", "demo", "app", "runtime.py"), encoding="utf-8").read())
run_src = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"), encoding="utf-8").read()
check("the loop's per-frame write goes through the pacing",
      "runtime.heartbeat(data_dir, force=bool(args.once)," in run_src
      and run_src.count("runtime.write(data_dir, running=True, pid=os.getpid(),") == 0)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
