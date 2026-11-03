-- 0005: which analytics function raised an event.
--
-- Function triggers already record this (systems/*/app/run.py writes
-- detail.function when a function's trigger fires, and outputs bind to it), but
-- it lived only inside the JSONB `detail` -- so it could not be filtered or
-- grouped, and an Event Card on the dashboard could only ever count ALL
-- violations, never "the violations MY variable raised".
--
-- Promoting it to a real column is the same move columns like camera and zone
-- already make: the attributes worth querying get indexed columns, the full
-- envelope stays in `detail` regardless.
--
-- Backfill reads the value out of the rows already stored, so history counts
-- the same as new events do rather than starting from empty.

ALTER TABLE events ADD COLUMN IF NOT EXISTS function TEXT;

UPDATE events
   SET function = detail ->> 'function'
 WHERE function IS NULL
   AND detail ? 'function';

CREATE INDEX IF NOT EXISTS events_function_idx ON events (system_slug, function);
