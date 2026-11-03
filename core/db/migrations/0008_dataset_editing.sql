-- 0008: let a person edit rows, without the log undoing them.
--
-- Rows arrive by travelling through events.jsonl, which the master replays
-- FROM OFFSET 0 on every boot (services/local_ingest.py -- deliberate: the
-- in-memory ring is empty after a restart, and the insert is idempotent on
-- event_id so history never double-counts).
--
-- That idempotence stops duplicates. It does NOT stop resurrection. A row
-- deleted by hand would be re-inserted by the very next replay: the operator
-- deletes it, sees it go, restarts the box a week later, and it is back with
-- no explanation. So a delete is a FLAG, not a DELETE -- the row stays, the
-- replay's ON CONFLICT DO NOTHING leaves the flag alone, and every read
-- filters it out.
--
-- Retention still removes these for real (repos/datasets.py prune_before);
-- a soft delete is about surviving replay, not about keeping data for ever.
ALTER TABLE dataset_rows ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- `edited` marks a row a person has changed, so a future replay of the same
-- event knows not to argue with them, and so the UI can say which values were
-- measured and which were typed.
ALTER TABLE dataset_rows ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;

-- Every read filters on deleted_at, so the common index has to carry it or
-- each page turn re-checks rows it will discard.
DROP INDEX IF EXISTS dataset_rows_ds_ts_idx;
CREATE INDEX IF NOT EXISTS dataset_rows_ds_ts_idx
    ON dataset_rows (system_slug, dataset, ts DESC) WHERE deleted_at IS NULL;
