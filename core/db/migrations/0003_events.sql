-- 0003: a queryable event store -- the analytics platform's foundation.
--
-- Until now events lived only in a 5000-item in-memory ring on the master,
-- wiped on restart (master_server.py). That cannot back history, filtering,
-- aggregation or a configurable event table. This persists them.
--
-- The DEVICE contract stays minimal (contract-v1 §3: an 8-field envelope with
-- everything system-specific in `detail`). The MASTER expands each event into
-- the wide row below on ingest -- the common attributes get real, indexed
-- columns for querying, and the full envelope is kept in `detail` (JSONB) so no
-- information is lost and future attributes need no migration.
--
-- One shared cytron_dashboard DB across a box's masters, so every row carries
-- system_slug; each master queries only its own.

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,          -- the device's event id (globally unique per contract)
    ts              TIMESTAMPTZ NOT NULL,      -- event time (device clock, offset-aware)
    system_slug     TEXT NOT NULL,             -- which system this belongs to
    host_serial     TEXT,                      -- device that produced it
    device_label    TEXT,                      -- human label at ingest time

    -- contract envelope
    type            TEXT,                      -- violation | info | alert | system
    severity        TEXT,                      -- ok | warn | crit
    label           TEXT,                      -- short human string
    snapshot_id     TEXT,

    -- common detection attributes, lifted out of `detail` for indexed querying.
    -- NULL when a given system/event does not produce them.
    camera          TEXT,
    zone            TEXT,
    confidence      REAL,
    track_id        TEXT,
    person_id       TEXT,
    object_id       TEXT,
    detection_label TEXT,                      -- the object class (person, car, ...)
    direction       TEXT,
    model           TEXT,

    detail          JSONB,                     -- the full envelope's `detail`, untouched
    status          TEXT DEFAULT 'open',       -- operator workflow: open | ack | closed
    notes           TEXT,                      -- operator notes (editable)

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Query patterns: newest-first by time, filtered by system + type/camera/zone,
-- and time-bucketed aggregation. Index the columns those touch.
CREATE INDEX IF NOT EXISTS events_system_ts_idx ON events (system_slug, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_idx      ON events (system_slug, type);
CREATE INDEX IF NOT EXISTS events_camera_idx    ON events (system_slug, camera);
CREATE INDEX IF NOT EXISTS events_zone_idx      ON events (system_slug, zone);
CREATE INDEX IF NOT EXISTS events_label_idx     ON events (system_slug, detection_label);
CREATE INDEX IF NOT EXISTS events_status_idx    ON events (system_slug, status);
