# Master database - `cytron_dashboard` (v2.0)

Until v2.0 the master held all state in memory (device sync results, events)
and in TOML config (paired devices). v2.0 activates the PostgreSQL database
declared in `config/storage.toml [postgres]` for the state that must persist
across restarts: **device folders, device display metadata, and the audit
log**.

## Degradation contract (load-bearing)

The master must never fail to boot or poll because `cytron_dashboard` is
unreachable. Enforced by design:

- `core/db.connect()` raises a typed `DbUnavailable`; `core/repos/*` read
  paths catch it and return `[]`/`{}`, write paths let it propagate so the
  route answers **503**.
- Migrations run best-effort at boot (`master_server.create_app`): a failure
  logs and continues.
- With the DB down: every device shows as **Unassigned**, folder/metadata
  writes 503, audit writes are skipped silently. **Polling, telemetry, the
  control channel and auth are all DB-independent and unaffected.**

## Layering

`core/db/` is an infrastructure tier that `core/repos/*` import for a
connection. Repos hold the only SQL in the codebase; `core/services/*` and
`core/routes/*` never import `core.db` or run SQL (docs/conventions.md §2).

## Migrations

Forward-only `*.sql` in `core/db/migrations/`, applied in sorted order, one
transaction each, recorded in `schema_migrations`. Idempotent-safe
(`IF NOT EXISTS`). CLI: `python -m core.db.migrations [--status]`.

## Schema (0001_master_init.sql)

### folders
Normalized parent-child hierarchy (no path strings). Rename/move a folder
without touching device rows.

| column     | type        | notes |
|------------|-------------|-------|
| id         | SERIAL PK   | |
| parent_id  | INT NULL FK → folders(id) ON DELETE CASCADE | deleting a folder removes nested folders |
| name       | TEXT NOT NULL | unique per parent (partial indexes: `(parent_id,name)`, and `(name) WHERE parent_id IS NULL`) |
| created_at | TIMESTAMPTZ | |

### device_meta
Master-owned display metadata. The device itself stays in
`slaves.local.toml` (token/url). No row, or `folder_id NULL` ⇒ **Unassigned**
(existing paired devices appear there automatically; a row is lazy-created on
first edit). Never affects device ID / auth / licensing / hardware identity.

| column       | type        | notes |
|--------------|-------------|-------|
| host_serial  | TEXT PK     | |
| display_name | TEXT        | precedence: display_name → slaves.toml label → agent display_name |
| description  | TEXT        | |
| notes        | TEXT        | |
| folder_id    | INT NULL FK → folders(id) ON DELETE SET NULL | folder delete returns its devices to Unassigned |
| created_at / updated_at | TIMESTAMPTZ | |

### audit_log
Centralized record of administrator actions. Best-effort writes (a DB outage
never blocks the action).

| column      | type          | notes |
|-------------|---------------|-------|
| id          | BIGSERIAL PK  | |
| ts          | TIMESTAMPTZ   | indexed DESC; `(action, ts DESC)` |
| username    | TEXT          | actor (`admin` or operator username) |
| host_serial | TEXT NULL     | device the action targeted, if any |
| action      | TEXT NOT NULL | canonical names in `core/services/audit.py ACTIONS` |
| result      | TEXT          | `ok` / `fail` |
| source_ip   | TEXT          | |
| detail      | JSONB         | action-specific context |

## Provisioning

Same `iriv`-style bootstrap the device side uses, but a separate database:
create role `cytron` + database `cytron_dashboard`, set the password in
`config/storage.local.toml [postgres].password`, then
`python -m core.db.migrations`. (A provisioning script mirroring the device
`setup_postgres.sh` can be added when the master install is scripted.)
