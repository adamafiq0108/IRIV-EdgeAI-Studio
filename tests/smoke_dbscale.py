# What keeps the database fast once it is actually big.
#
# These are cheap invariants standing in for an expensive truth: a Pi with a
# year of events in Postgres. Each one pins a decision that only shows up as a
# symptom months after it is made, when nobody connects the slow dashboard to
# the line of SQL that caused it.
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

import core.db as db                                         # noqa: E402
from core.repos import datasets as ds                        # noqa: E402
from core.repos import events as ev                          # noqa: E402

MIG = os.path.join(ROOT, "core", "db", "migrations")
sql = "\n".join(
    open(os.path.join(MIG, f), encoding="utf-8").read()
    for f in sorted(os.listdir(MIG)) if f.endswith(".sql"))

# 1. THE PRUNE MUST BE INDEXED. `DELETE ... WHERE ts < %s` cannot use an index
#    that leads with system_slug, so without one of these the retention sweep
#    was a full sequential scan of the whole table, every six hours, for ever.
assert re.search(r"CREATE INDEX[^;]*events_ts_idx[^;]*ON events \(ts\)", sql), \
    "no index on events.ts -- the retention prune is a sequential scan"
assert re.search(r"CREATE INDEX[^;]*dataset_rows_ts_idx[^;]*\(ts\)", sql), \
    "no index on dataset_rows.ts"
print("== the retention prune has an index to stand on")

# 2. ...AND MUST BE BATCHED. A year of backlog in one unbounded DELETE holds
#    its transaction, locks and WAL open for the whole run, and being cut off
#    means starting from nothing next sweep.
for repo, name in ((ev, "events"), (ds, "dataset_rows")):
    assert 0 < repo.PRUNE_BATCH <= 20000, (name, repo.PRUNE_BATCH)
    src = open(repo.__file__, encoding="utf-8").read()
    body = src.split("def prune_before")[1].split("\ndef ")[0]
    assert "LIMIT" in body, f"{name} prune is unbounded"
    assert "while True" in body, f"{name} prune is not batched"
    assert body.count("commit()") >= 1, f"{name} prune never commits per batch"
print("== both prunes delete in batches of", ev.PRUNE_BATCH, "and commit as they go")

# 3. NO UNBOUNDED count(*). An exact count reads every matching row; it ran on
#    every ten-second refresh of a table with a year of history.
for repo, name in ((ev, "events"), (ds, "dataset_rows")):
    src = open(repo.__file__, encoding="utf-8").read()
    body = src.split("def query(")[1].split("\ndef ")[0]
    assert "count(*)" in body and "LIMIT" in body, name
    assert f"{{COUNT_CAP + 1}}" in body, f"{name} count is not capped"
    assert 0 < repo.COUNT_CAP <= 100000, (name, repo.COUNT_CAP)
print("== row counts stop at", ev.COUNT_CAP, "and say so, instead of scanning")

# and the shape carries the fact, so the page can render "20000+" rather than
# a number that looks exact and is not
assert ds.query("nope", "nope", [{"name": "a", "type": "text"}]) == \
    {"rows": [], "total": 0, "capped": False}
print("== the result says whether the count was capped")

# 4. EVERY STATEMENT HAS A CEILING. connect_timeout only bounds the handshake;
#    without statement_timeout a query over a year of rows blocks its request
#    thread for as long as Postgres will keep working, and a page refreshing
#    every four seconds stacks those threads until the box stops answering.
src = open(db.__file__, encoding="utf-8").read()
assert "statement_timeout" in src, "no per-statement ceiling"
assert 0 < db.STATEMENT_TIMEOUT_MS <= 30000, db.STATEMENT_TIMEOUT_MS
assert db.WRITE_TIMEOUT_MS > db.STATEMENT_TIMEOUT_MS, "writes need longer than reads"
print(f"== reads are capped at {db.STATEMENT_TIMEOUT_MS}ms, "
      f"writes at {db.WRITE_TIMEOUT_MS}ms")

# 5. THE GIN INDEX MUST MATCH THE OPERATOR THE REPO ACTUALLY EMITS.
#    0006 indexed cols with the default jsonb_ops and filtered with
#    `cols->>'name' = %s`. GIN serves containment and existence, never ->>, so
#    the index slowed every insert and accelerated no read at all.
assert "jsonb_path_ops" in sql, "the GIN index is not the containment opclass"
where, params = ds._where("s", "t", {"zone": "text"}, {"zone": "Bay"}, None, None)
assert "@>" in where and "cols->>" not in where, where
assert '{"zone": "Bay"}' in params, params
print("== text filters emit containment, which is what the GIN index answers")

# 6. GROWTH IS BOUNDED AT ALL, and the sweep refreshes planner statistics --
#    after a big delete Postgres still believes the row counts it measured
#    before it, which is how an index scan silently becomes a sequential one.
from core.services import retention as rt                     # noqa: E402
assert rt.DEFAULTS["events_months"] >= 1
assert rt.SWEEP_INTERVAL_S <= 24 * 3600, rt.SWEEP_INTERVAL_S
rt_src = open(rt.__file__, encoding="utf-8").read()
assert "analyze" in rt_src, "the sweep never refreshes planner statistics"
assert hasattr(ev, "analyze")
print("== growth is bounded at", rt.DEFAULTS["events_months"],
      "months, and the sweep re-ANALYZEs after deleting")

# 7. THE LOCAL INGEST MUST NOT RIDE THE SLAVE POLL. That interval ships at
#    600s; with a 2000-row cap it capped ingestion at ~3.3 rows/s, and a
#    system writing faster did not slow down -- it LOST rows, because the log
#    rotated past the reader's cursor.
ms = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
assert "_local_loop" in ms
# Read from the source rather than importing: master_server pulls in uvicorn,
# which is a master-only dependency and absent on a dev box.
interval = re.search(r"^LOCAL_INGEST_INTERVAL_S = (\d+)", ms, re.M)
assert interval, "no LOCAL_INGEST_INTERVAL_S"
LOCAL_INGEST_INTERVAL_S = int(interval.group(1))
assert 0 < LOCAL_INGEST_INTERVAL_S <= 30, LOCAL_INGEST_INTERVAL_S
# Just _sync_loop's own body: up to the next top-level def of either kind.
loop = re.split(r"\n(?:async )?def ",
                ms.split("async def _sync_loop")[1])[0]
assert "_apply_local" not in loop, \
    "local ingest is back on the slave poll -- that is the data-loss path"
local = re.split(r"\n(?:async )?def ",
                 ms.split("async def _local_loop")[1])[0]
assert "_apply_local" in local, "the local loop does not actually drain the log"
assert "ingest_lock" in ms, "two loops rewrite app.state.events with no lock"
print("== local ingest runs every", LOCAL_INGEST_INTERVAL_S,
      "s on its own loop, not the 600s slave poll")

# 8. NO PAGE POLLS A HIDDEN TAB. Each Analytics refresh costs one query per
#    database-backed widget; a forgotten background tab ran that sweep every
#    four seconds to render pixels nobody was looking at.
for page in ("Analytics", "EventTable"):
    src = open(os.path.join(ROOT, "frontend", "src", "pages", f"{page}.jsx"),
               encoding="utf-8").read()
    assert "useVisibleInterval" in src, f"{page} polls unconditionally"
    assert "setInterval(" not in src, f"{page} still has a raw setInterval"
print("== Analytics and Events stop polling when hidden")

# The Database tool does not poll AT ALL: it is a spreadsheet, and a grid that
# reloads under the cursor while somebody is reading a row is worse than one
# that waits to be asked. It has a Refresh button instead.
src = open(os.path.join(ROOT, "frontend", "src", "pages", "Database.jsx"),
           encoding="utf-8").read()
assert "setInterval(" not in src, "the Database tool polls -- it should not"
assert "Refresh" in src, "no way to reload by hand"
print("== the Database tool never polls; it refreshes when asked")
print("ALL OK")
