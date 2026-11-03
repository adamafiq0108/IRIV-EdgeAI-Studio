# "Reset counters": the dashboard's numbers go back to zero, and the footage
# and event log an operator may still need are NOT collateral.
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import retention as rt                   # noqa: E402
from core.services import detector_control as dc            # noqa: E402

SAND = tempfile.mkdtemp()
SLUG = "counters_demo"
PKG = os.path.join(SAND, SLUG)
DATA = os.path.join(PKG, "data")
os.makedirs(os.path.join(DATA, "snapshots"), exist_ok=True)
os.makedirs(os.path.join(DATA, "clips"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{DATA.replace(os.sep, "/")}"\n')


def seed():
    """Everything a system accumulates: tallies, events, footage."""
    with open(os.path.join(DATA, "fn_daily.json"), "w", encoding="utf-8") as f:
        json.dump({"counter": {"2026-08-06": 42}}, f)
    with open(os.path.join(DATA, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"id": 1}\n')
    for name in ("snapshots/a.jpg", "clips/a.mp4"):
        with open(os.path.join(DATA, name), "wb") as f:
            f.write(b"x")


def present():
    return {
        "tallies": os.path.exists(os.path.join(DATA, "fn_daily.json")),
        "events": os.path.exists(os.path.join(DATA, "events.jsonl")),
        "snapshot": os.path.exists(os.path.join(DATA, "snapshots/a.jpg")),
        "clip": os.path.exists(os.path.join(DATA, "clips/a.mp4")),
    }


# 1. the scope exists and is refused if misspelled
assert "counters" in rt.PURGE_SCOPES, rt.PURGE_SCOPES
try:
    rt.purge_system(SAND, SLUG, "counterz")
except ValueError:
    pass
else:
    raise AssertionError("an unknown scope was accepted")
print("== 'counters' is a real scope; a typo is refused, not guessed at")

# 2. it clears the per-day tallies -- what today / week / month reads
seed()
removed = rt.purge_system(SAND, SLUG, "counters")
state = present()
assert removed["tallies"] == 1, removed
assert state["tallies"] is False, "the per-day tallies survived the reset"
print("== the per-day tallies are cleared:", removed["tallies"], "file")

# 3. the event log goes too -- it HAS to. The Postgres rows are a projection
#    of that log and the master re-reads it from offset 0 on every boot
#    (core/services/local_ingest.py), so clearing the rows alone means they
#    come back at the next restart and the numbers with them.
assert state["events"] is False, \
    "the event log survived, so the DB rows will be re-ingested on next boot"
print("== the event log goes with them: the DB rows are a copy of it")

# 4. FOOTAGE is kept -- that is the whole point of it not being 'all'
assert state["snapshot"] is True and state["clip"] is True, \
    "resetting counters deleted footage"
print("== snapshots and clips are kept -- evidence is not a counter")

# 5. it is safe to run when there is nothing to clear
removed = rt.purge_system(SAND, SLUG, "counters")
assert removed["tallies"] == 0, removed
print("== running it again is a no-op, not an error")

# 6. the other scopes still do exactly what they did, and leave tallies alone
seed()
rt.purge_system(SAND, SLUG, "footage")
state = present()
assert state["snapshot"] is False and state["clip"] is False
assert state["tallies"] is True, "the footage scope cleared the counters"
assert state["events"] is True
seed()
rt.purge_system(SAND, SLUG, "events")
state = present()
assert state["events"] is False
assert state["tallies"] is True, "the events scope cleared the counters"
assert state["snapshot"] is True
print("== 'events' and 'footage' are unchanged and never touch the tallies")

# 7. a database that is down must not fail the part that worked
from core.repos import events as events_repo                # noqa: E402
assert events_repo.delete_for_system("nothing_here") == 0
assert events_repo.delete_for_system("") == 0
print("== the event-store delete is scoped to one slug and survives no DB")
print("ALL OK")
