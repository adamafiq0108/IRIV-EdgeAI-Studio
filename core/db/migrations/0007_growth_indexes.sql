-- 0007: make the two growing tables stay fast once they are actually big.
--
-- `events` and `dataset_rows` grow for a year before retention touches them
-- (retention.py events_months = 12). Three things degrade as they do, and all
-- three are index problems rather than query problems.

-- 1. THE RETENTION PRUNE HAD NO INDEX AT ALL.
--
-- `DELETE FROM events WHERE ts < %s` (repos/events.py prune_before) cannot use
-- events_system_ts_idx, because that index leads with system_slug and the
-- predicate does not mention it. Every sweep -- one every six hours, for ever
-- -- was a sequential scan of the whole table. On a year of rows that is the
-- single most expensive thing the box does, and it happens on a schedule
-- nobody is watching.
--
-- Indexing ts alone costs one btree insert per row. The ingest ceiling is a
-- few rows a second, so that is nothing; the scan it removes is everything.
CREATE INDEX IF NOT EXISTS events_ts_idx       ON events (ts);
CREATE INDEX IF NOT EXISTS dataset_rows_ts_idx ON dataset_rows (ts);

-- 2. THE GIN INDEX ON dataset_rows.cols WAS DEAD WEIGHT.
--
-- 0006 created it saying "grouping/filtering happens on cols, hence the GIN
-- index". That was wrong. The default jsonb_ops opclass indexes the
-- containment and existence operators (@>, ?, ?|, ?&) and nothing else, while
-- every filter the repo generated was `cols->>'name' = %s` -- which no GIN
-- index can serve. So it slowed down every insert and accelerated no read.
--
-- Rather than drop it, the repo now emits `cols @> '{"name":"value"}'::jsonb`
-- for text and datetime equality (repos/datasets.py _where), which IS what
-- this index is for. jsonb_path_ops is roughly half the size and faster for
-- exactly that operator, which is the only one used.
DROP INDEX IF EXISTS dataset_rows_cols_idx;
CREATE INDEX IF NOT EXISTS dataset_rows_cols_idx
    ON dataset_rows USING GIN (cols jsonb_path_ops);

-- 3. FILTER + TIME WINDOW HAD NO COMPOSITE.
--
-- The common dashboard question is "this type / this camera, over this
-- period". With separate (system_slug, type) and (system_slug, ts) indexes
-- Postgres picks one and filters the rest by hand, which on a year of rows
-- means reading far more than it returns. These two cover the pairs that
-- actually get asked -- a card scoped to one function, and the event table
-- filtered by type.
CREATE INDEX IF NOT EXISTS events_fn_ts_idx   ON events (system_slug, function, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts_idx ON events (system_slug, type, ts DESC);

-- Planner statistics. Nothing in the product has ever run ANALYZE, so after
-- the first large prune the planner's row estimates are whatever they were a
-- year ago -- which is how a query that was an index scan silently becomes a
-- sequential one. The sweep runs it from now on (retention.py _sweep_db);
-- this is the initial pass, over tables that may never have had one.
ANALYZE events;
ANALYZE dataset_rows;
