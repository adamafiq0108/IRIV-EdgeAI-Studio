# -*- coding: utf-8 -*-
"""
events.py -- the Events Log page's Clear events, as plain values.

The route (core/routes/events.py clear_events) checks who is asking and writes
the audit row; this decides what "every event of this system" means and in
what order it goes. No framework imports, no SQL (docs/conventions.md sec 2):
the rows are deleted by core/repos/events.py.

WHAT GOES, for ONE system only (the slug is the master's own, never the
client's):

  * its rows in the event store (the table, the filters, every query widget);
  * the live event log those rows are a projection of, its rotation and its
    per-day archives -- the same files Settings' per-system "Events log"
    delete removes (retention scope "events"). Without them the master
    re-reads events.jsonl from offset 0 at its next boot
    (core/services/local_ingest.py) and every cleared row comes back;
  * the master's in-memory ring of recent events, which the Live page's
    alerts and the widgets' no-database fallback read. Left alone, the page
    would say "0 events" beside a dashboard still listing the old ones.

WHAT STAYS: snapshots, clips and their thumbnails, the operator's dataset
tables, device metadata, the audit trail, the System Logs, the rules' own
tallies (fn_daily.json) and the running totals LocalIngest keeps in memory.

ORDER. The database goes first and STRICTLY: when Postgres is down nothing
else is touched and the caller is told so, rather than "0 events cleared"
over a store nobody reached, with the files already gone and the rows due to
come back. Once the rows are gone, the files and the ring are best effort --
the clear has happened, and a file that will not delete is logged, not
turned into an error about a clear that worked.
"""

import logging

from core.repos import events as events_repo

logger = logging.getLogger("cytron.events")


def clear(slug, config_dir=None, systems_dir=None, ring=None):
    """Clear every stored event of `slug`. Returns {"removed": rows,
    "files": {...}}; `removed` is the event-store row count the page reports.

    Raises what the repo raises when the database cannot be reached or the
    delete fails (core.db.DbUnavailable, or the driver's error), having
    changed nothing. `ring` is the master's in-memory event list, emptied in
    place because the sync loop holds the same object."""
    removed = events_repo.delete_for_system(slug, config_dir, strict=True)
    files = {}
    if systems_dir:
        try:
            from core.services import retention       # noqa: PLC0415 -- heavy
            files = retention.purge_system(systems_dir, slug, "events")
        except Exception:                             # noqa: BLE001
            logger.warning("[EVENTS] %s: event files not cleared", slug,
                           exc_info=True)
    if ring is not None:
        del ring[:]
    return {"removed": removed, "files": files}
