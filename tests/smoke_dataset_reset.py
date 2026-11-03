# Resetting a system's counters must clear its DATASETS too.
#
# Dataset rows are a projection of events.jsonl, exactly like the `events`
# table. Clearing the log and the events but not these would strand rows that
# no replay can restore (the log they came from is gone) and no reset can reach
# (nothing else deletes them) -- a table that quietly outlives its own history.
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo             # noqa: E402
from core.services import retention as rt                    # noqa: E402

SAND = tempfile.mkdtemp()
SLUG = "dataset_demo"
PKG = os.path.join(SAND, SLUG)
DATA = os.path.join(PKG, "data")
os.makedirs(os.path.join(DATA, "snapshots"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{DATA.replace(os.sep, "/")}"\n')


def seed():
    with open(os.path.join(DATA, "fn_daily.json"), "w", encoding="utf-8") as f:
        json.dump({"counter": {"2026-08-06": 42}}, f)
    with open(os.path.join(DATA, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"id": 1}\n')
    with open(os.path.join(DATA, "snapshots", "a.jpg"), "wb") as f:
        f.write(b"x")


# There is no Postgres on a dev box, so stand in for the repo and record what
# the reset actually asked it to do. The point under test is the CALL -- that
# resetting a system reaches the dataset rows at all -- not the DELETE itself.
called = []
datasets_repo.delete_for_system = lambda slug, config_dir=None: (
    called.append(slug) or 7)

# 1. resetting the counters clears this system's dataset rows
seed()
called.clear()
removed = rt.purge_system(SAND, SLUG, "counters")
assert called == [SLUG], called
assert removed["dataset_rows"] == 7, removed
print("== reset counters cleared", removed["dataset_rows"], "dataset rows for", called[0])

# 2. scoped to ONE system: another system's tables are somebody else's history
assert SLUG in called and len(set(called)) == 1, called
print("== and only that system's -- the delete is scoped to the slug")

# 3. it goes together with the event log, in the same pass. If the log survived
#    a reset, the master would re-read it from offset 0 on the next boot and
#    put every row back.
assert not os.path.exists(os.path.join(DATA, "events.jsonl")), \
    "the log survived, so the rows would be re-ingested on the next boot"
print("== the log they are a projection of goes in the same pass")

# 4. footage is still not collateral
assert os.path.exists(os.path.join(DATA, "snapshots", "a.jpg")), \
    "resetting counters deleted footage"
print("== snapshots are kept -- evidence is not a counter")

# 5. the retention sweep ages them out on the same cutoff as the events
pruned = []
datasets_repo.prune_before = lambda cutoff, config_dir=None: (
    pruned.append(cutoff) or 3)
from core.repos import events as events_repo                 # noqa: E402
events_cutoff = []
events_repo.prune_before = lambda cutoff, config_dir=None: (
    events_cutoff.append(cutoff) or 0)
report = {}
rt._sweep_db(rt.policy(), report)
assert pruned and events_cutoff and pruned[0] == events_cutoff[0], \
    (pruned, events_cutoff)
assert report.get("dataset_pruned") == 3, report
print("== the sweep ages datasets out on the same cutoff as events")

# 6. with a real (absent) database, the delete is a quiet 0 rather than a crash
import importlib                                             # noqa: E402
importlib.reload(datasets_repo)
assert datasets_repo.delete_for_system("nothing_here") == 0
assert datasets_repo.delete_for_system("") == 0
assert datasets_repo.prune_before(0) == 0
print("== with no database reachable it is a quiet 0, never an error")
print("ALL OK")
