-- 0004: scope the audit log to a system.
--
-- audit_log lives in the shared cytron_dashboard DB, but each master serves one
-- system. Without a system tag, one system's Audit page showed every other
-- system's entries too. Add a `system` column; each master stamps its own slug
-- on write and filters to it on read.
--
-- No backfill: existing rows keep system = NULL. They are hub/unattributed
-- entries and remain visible only in an unfiltered (hub) view, not on any one
-- system's page -- which is the correct, conservative default.
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS system TEXT;
CREATE INDEX IF NOT EXISTS audit_system_idx ON audit_log (system, ts DESC);
