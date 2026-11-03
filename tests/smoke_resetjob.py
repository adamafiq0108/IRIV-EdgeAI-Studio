# The counters reset runs in the BACKGROUND: the button answers at once, the
# status says which step it is on, and a page that opens mid-reset can tell.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import retention as rt                   # noqa: E402
from core.services import detector_control as dc            # noqa: E402

SAND = tempfile.mkdtemp()
SLUG = "resetjob_demo"
DATA = os.path.join(SAND, SLUG, "data")
os.makedirs(os.path.join(DATA, "snapshots"), exist_ok=True)
with open(os.path.join(SAND, SLUG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{DATA.replace(os.sep, "/")}"\n')


def seed():
    with open(os.path.join(DATA, "fn_daily.json"), "w", encoding="utf-8") as f:
        json.dump({"counter": {"2026-08-06": 42}}, f)
    with open(os.path.join(DATA, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"id": 1}\n')
    with open(os.path.join(DATA, "snapshots", "a.jpg"), "wb") as f:
        f.write(b"x")


def wait_done(slug, limit=10.0):
    end = time.time() + limit
    while time.time() < end:
        st = rt.reset_status(slug)
        if st["state"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"reset never finished: {rt.reset_status(slug)}")


# The app is not really running in a test, so stop/start are no-ops that must
# not break the job. Stub them to prove the ORDER the job uses.
calls = []
dc.status = lambda root, slug: {"running": True}
# The whole app (stop_app): its tallies live in memory with the AI on or off.
dc.stop_app = lambda root, slug: calls.append(("stop", os.path.exists(
    os.path.join(DATA, "fn_daily.json"))))
# The reset's own Start is unwatched (nobody is at the page to read a
# refusal), so it passes require_camera=False and leaves a missing camera to
# the app's wait; the stub records the flag to pin that.
starts_kw = []
dc.start = lambda root, slug, **kw: starts_kw.append(kw) or calls.append((
    "start", os.path.exists(os.path.join(DATA, "fn_daily.json"))))

# 1. it returns IMMEDIATELY, already reporting itself as running
seed()
bounced = []
began = time.time()
first = rt.start_counter_reset(SAND, SLUG, restart=lambda: bounced.append(1) or True)
elapsed = time.time() - began
assert first["state"] == "running", first
assert elapsed < 0.5, f"the call blocked for {elapsed:.2f}s -- it must not"
print(f"== the call returns in {elapsed * 1000:.0f}ms, already 'running'")

# 2. it finishes, and reports what it removed
done = wait_done(SLUG)
assert done["state"] == "done", done
assert done["removed"]["tallies"] == 1, done["removed"]
assert done["restarted"] is True, "the master was never bounced"
assert bounced, "the restart callback never ran"
print("== it finishes on its own and reports what went:", done["removed"])

# 3. THE ORDER: detection is stopped BEFORE the delete and started after.
#    Getting this wrong is silent -- the app rewrites the tallies it holds in
#    memory and the reset undoes itself.
assert [c[0] for c in calls] == ["stop", "start"], calls
assert calls[0][1] is True, "the tallies were already gone when detection stopped"
assert calls[1][1] is False, "detection restarted before the tallies were cleared"
assert starts_kw == [{"require_camera": False}],     f"the reset's Start must not refuse for a missing camera: {starts_kw}"
print("== order proven: stop -> delete -> start")

# 4. footage is kept; the event log goes (the DB rows are a copy of it)
assert os.path.exists(os.path.join(DATA, "snapshots", "a.jpg"))
assert not os.path.exists(os.path.join(DATA, "events.jsonl"))
print("== snapshots kept, event log cleared")

# 5. a second press while one runs does not start a second job
seed()
rt._RESETS[SLUG] = {"state": "running", "step": "clearing counters",
                    "started": time.time(), "finished": 0}
again = rt.start_counter_reset(SAND, SLUG, restart=lambda: True)
assert again["state"] == "running", again
assert os.path.exists(os.path.join(DATA, "fn_daily.json")), \
    "a second reset ran while the first was in flight"
print("== pressing it twice does not start a second reset")

# 6. an in-flight reset is visible to any page that asks
assert SLUG in rt.resetting_slugs(), rt.resetting_slugs()
rt._RESETS[SLUG] = {"state": "done", "step": "done", "finished": time.time()}
assert SLUG not in rt.resetting_slugs()
assert rt.reset_status("never_reset")["state"] == "idle"
print("== a running reset is listed for the hub's cards; a finished one is not")

# 7. finished jobs are reaped, so the dict cannot grow forever
rt._RESETS[SLUG] = {"state": "done", "step": "done",
                    "finished": time.time() - rt.RESET_TTL_S - 1}
assert rt.reset_status(SLUG)["state"] == "idle", "a stale job was never reaped"
print("== a finished job is forgotten after its TTL")
print("ALL OK")
