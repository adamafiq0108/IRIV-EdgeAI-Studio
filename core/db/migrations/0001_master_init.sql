-- 0001_master_init.sql
-- Master persistence for IRIV-Vision-Studio v2.0 (cytron_dashboard database).
-- Everything IF NOT EXISTS: safe to re-run, safe on a partially-provisioned DB.

-- User-defined device folder hierarchy. Normalized parent-child (NO path
-- strings): rename/move a folder without touching device rows. Deleting a
-- folder cascades to nested folders and SET NULLs the devices in them (they
-- fall back to Unassigned).
CREATE TABLE IF NOT EXISTS folders (
    id         SERIAL PRIMARY KEY,
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Sibling names unique; top-level (parent_id NULL) names unique too. Two
-- partial indexes because NULL is distinct in a plain UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS folders_sibling_name
    ON folders (parent_id, name) WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS folders_root_name
    ON folders (name) WHERE parent_id IS NULL;

-- Master-owned display metadata for a device. The device itself stays in
-- slaves.local.toml (token/url); this is purely presentation + organization
-- and never affects device ID / auth / licensing / hardware identity.
-- No row (or folder_id NULL) = Unassigned.
CREATE TABLE IF NOT EXISTS device_meta (
    host_serial  TEXT PRIMARY KEY,
    display_name TEXT,
    description  TEXT,
    notes        TEXT,
    folder_id    INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Centralized audit of administrator actions.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    username    TEXT,
    host_serial TEXT,
    action      TEXT NOT NULL,
    result      TEXT,
    source_ip   TEXT,
    detail      JSONB
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log (action, ts DESC);
