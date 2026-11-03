-- 0006: user-defined tables ("datasets") recorded from a workflow.
--
-- A workflow could publish NUMBERS (stat keys) and fire EVENTS, but not record
-- a ROW. "Every time someone crosses the line, log the time, camera, zone,
-- track id and dwell seconds as one record" had nowhere to go: the numbers
-- landed in unrelated stats, and `events.detail` is not queryable because the
-- filterable column set there is fixed by the schema (repos/events.py).
--
-- The Dataset node lets an operator declare their own columns. Each write
-- produces one row here.
--
-- WHY event_id IS THE PRIMARY KEY, and not a serial:
-- the detection app has no database access -- it imports nothing from core/ --
-- so a dataset row travels the same road as everything else, appended to
-- events.jsonl with detail.dataset set, and expanded on ingest. That log is
-- re-read FROM OFFSET 0 on every master boot (services/local_ingest.py: the
-- database is a projection of the log, never the source of truth). Keying on
-- the source event's id makes that replay a no-op instead of a duplicate.
--
-- WHY A SEPARATE TABLE, and not more columns on `events`:
-- dataset writes can be timer-paced (a row every few seconds), so they are the
-- highest-volume rows on the box. Folding them into `events` would inflate all
-- six of its indexes for rows no event query ever wants.

CREATE TABLE IF NOT EXISTS dataset_rows (
    event_id    TEXT PRIMARY KEY,          -- the source event's id: replay is idempotent
    ts          TIMESTAMPTZ NOT NULL,      -- when the row was recorded (device clock)
    system_slug TEXT NOT NULL,             -- one shared DB across a box's masters
    dataset     TEXT NOT NULL,             -- which dataset (the node's key)
    cols        JSONB NOT NULL,            -- {column_name: value}, per the node's definition
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The only access pattern: one dataset of one system, newest first, optionally
-- time-bounded. Grouping/filtering happens on `cols`, hence the GIN index.
CREATE INDEX IF NOT EXISTS dataset_rows_ds_ts_idx ON dataset_rows (system_slug, dataset, ts DESC);
CREATE INDEX IF NOT EXISTS dataset_rows_cols_idx  ON dataset_rows USING GIN (cols);
