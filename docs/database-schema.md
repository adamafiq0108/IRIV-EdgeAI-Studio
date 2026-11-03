# Data and schema

Living document - updated as the product changes.

Where every durable fact on an IRIV Edge AI box lives, and why it lives there rather than
somewhere else. This is the Design-phase counterpart to `docs/architecture.md`: that document
describes the processes, this one describes what they persist.

Persistence on this product is deliberately split three ways, and the split is the design:

| Store | Holds | Survives |
|---|---|---|
| PostgreSQL (`cytron_dashboard`) | rolled-up events, operator-defined table rows, device folders and metadata, the audit log | a restart; **not** required for the box to run |
| TOML files under `config/` and per system | configuration, credentials, the whole no-code build (zones, functions, outputs, cameras) | a restart, and - for the per-system half - an OTA update |
| Append-only files under each system's data dir and the store root | the raw event log, snapshots, clips, history day-files, layouts, workflows | everything, including the database being wiped |

The ordering matters. **The database is a projection of the log, never the source of truth.**
A detection app has no database access at all - it imports nothing from `core/` - so every row
that reaches Postgres first travelled through `events.jsonl` on disk, and the master replays that
log from offset 0 on every boot (`core/db/migrations/0006_dataset_rows.sql:12-18`). Losing the
database costs query speed and nothing else; losing the log costs data.

---

## 1. The database

### 1.1 Connection and degradation

Settings come from `config/storage.toml [postgres]`, layered with `storage.local.toml` for the
password. `core/db/pg_settings()` (`core/db/__init__.py:29-36`) merges them over hard defaults -
`localhost:5432`, database `cytron_dashboard`, user `cytron`, empty password - so a box whose
config block is missing entirely still tries the conventional local cluster instead of failing on
a `KeyError`.

Two ceilings are set on the server side of every connection, because a connect timeout only
bounds the handshake and says nothing about a query that has been running for ten seconds:

- `STATEMENT_TIMEOUT_MS = 10000` (`core/db/__init__.py:47`) - the default. An analytics page that
  refreshes every four seconds would otherwise stack blocked request threads until the box stops
  answering. A dashboard query that takes ten seconds has already failed; failing it here turns a
  hang into an empty widget, which every repo read already knows how to render.
- `WRITE_TIMEOUT_MS = 120000` (`core/db/__init__.py:51`) - passed explicitly by the retention
  prune, which legitimately deletes a year of rows and would otherwise be cut off mid-`DELETE`
  and repeat the same work next sweep.

`connect()` raises the typed `DbUnavailable` on any failure (`core/db/__init__.py:22-24`,
`:76-77`), including `psycopg2` not being installed at all. That type is the whole degradation
contract: repo read paths catch it and return `[]`/`{}`, write paths let it propagate so the route
answers 503, and migrations run best-effort at master boot with a warning
(`master_server.py:482-487`). With Postgres down, every device shows as **Unassigned**, folder and
metadata writes 503, audit writes skip silently - and polling, telemetry, the control channel,
video and auth are all unaffected, because none of them touch the database.

### 1.2 Layering

`core/db/` is an infrastructure tier. `core/repos/*.py` import it for a connection and hold the
only SQL in the codebase; `core/services/` and `core/routes/` never import `core.db` and never
write SQL (`docs/conventions.md` §2).

### 1.3 Migrations

Forward-only `*.sql` files in `core/db/migrations/`, applied in sorted filename order, one
transaction each, recorded in `schema_migrations` so a re-run skips them. Everything is written
`IF NOT EXISTS`, so a partially-provisioned database is safe to migrate.

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
(`core/db/migrations/__init__.py:23-28`)

Run by hand with `python -m core.db.migrations [--status]`; run automatically, best-effort, by
every master at boot.

One database is shared by every master on a box. That is why almost every table carries a
`system_slug`/`system` column: each master filters to its own slug so one system's folders,
events and audit entries never appear on another's pages.

### 1.4 `folders`

User-defined device hierarchy. Normalised parent-child with no path strings, so renaming or moving
a folder touches no device row.

| column | type | notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `parent_id` | `INTEGER REFERENCES folders(id) ON DELETE CASCADE` | deleting a folder removes nested folders |
| `name` | `TEXT NOT NULL` | |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `system` | `TEXT` | added by `0002`; untagged rows are invisible to every master |

Indexes: `folders_sibling_name` on `(parent_id, name) WHERE parent_id IS NOT NULL` and
`folders_root_name` on `(system, name) WHERE parent_id IS NULL`. Two partial indexes rather than
one `UNIQUE`, because NULL is distinct in a plain unique constraint and top-level folders would
otherwise be free to collide (`0001_master_init.sql:17-20`, `0002_folder_system.sql:20-22`).

`0002` deliberately does **not** backfill `system`. The product cannot know which system a
deployment's existing folders belonged to, and a wrong guess moves real devices into the wrong
dashboard - invisible and unrecoverable. An untagged folder is merely invisible, which an operator
can fix with one `UPDATE` (`0002_folder_system.sql:7-15`).

Written by `core/repos/folders.py` - `create` (`:32`), `rename` (`:47`), `move` (`:59`),
`delete` (`:72`). Read by `list_all` (`:11`) and `descendant_ids` (`:86`), which
`core/services/inventory.py` folds into the sidebar snapshot the master caches on
`app.state.folders` and refreshes once per sync cycle (`master_server.py:194`).

### 1.5 `device_meta`

Master-owned display metadata for a paired device. The device *itself* - its URL and bearer token -
stays in `slaves.local.toml`, because polling must keep working during a Postgres outage and a
credential cannot hard-depend on the database.

| column | type | notes |
|---|---|---|
| `host_serial` | `TEXT PRIMARY KEY` | the Pi CPU serial (`docs/contract-v1.md` §5) |
| `display_name` | `TEXT` | precedence: this → `slaves.toml` label → the agent's own `display_name` |
| `description` | `TEXT` | |
| `notes` | `TEXT` | |
| `folder_id` | `INTEGER REFERENCES folders(id) ON DELETE SET NULL` | deleting a folder returns its devices to Unassigned |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

No row, or `folder_id NULL`, means **Unassigned** - so devices paired before this table existed
appear there automatically and a row is lazy-created on the first edit. Nothing here ever affects
device identity, auth, licensing or hardware (`0001_master_init.sql:22-25`).

Written by `core/repos/device_meta.py` `upsert` (`:49`) and `set_folder` (`:73`) from the Manage
page's routes. Read by `list_all` (`:13`) and `get` (`:31`), which `core/services/inventory.py`
merges onto each polled device in the sync loop (`master_server.py:192-193`).

### 1.6 `audit_log`

Centralised record of administrator actions, shared across a box's masters. Writes are
best-effort: a database outage never blocks the action being audited.

| column | type | notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `ts` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `username` | `TEXT` | the actor |
| `host_serial` | `TEXT` | the device the action targeted, if any |
| `action` | `TEXT NOT NULL` | canonical names in `core/services/audit.py` |
| `result` | `TEXT` | `ok` / `fail` |
| `source_ip` | `TEXT` | |
| `detail` | `JSONB` | action-specific context |
| `system` | `TEXT` | added by `0004`; each master stamps its own slug |

Indexes: `audit_log_ts_idx (ts DESC)`, `audit_log_action_idx (action, ts DESC)`,
`audit_system_idx (system, ts DESC)`.

`0004` leaves existing rows at `system = NULL` on purpose: they are hub or unattributed entries,
and they stay visible in the unfiltered hub view rather than being guessed onto one system's page
(`0004_audit_system.sql:8-10`).

Written by `core/repos/audit.py record()` (`:13`), always through `core/services/audit.py`, which
holds the action vocabulary and the per-master slug set at startup
(`master_server.py:459`). Read by `query()` (`:36`) behind `/api/audit`, mounted on both the hub
and every master.

### 1.7 `events`

The queryable event store, and the foundation everything analytic sits on. Before `0003` events
lived only in the 5000-item in-memory ring on the master (`master_server.py:83`), wiped on every
restart - which cannot back history, filtering, aggregation or a configurable event table.

The device contract stays deliberately minimal: an eight-field envelope with everything
system-specific inside `detail` (`docs/contract-v1.md` §3). The **master** is what expands each
envelope into the wide row below on ingest. Common attributes get real, indexed columns so they
can be queried; the untouched `detail` object rides along so nothing is lost and a future
attribute needs no migration.

| column | type | notes |
|---|---|---|
| `event_id` | `TEXT PRIMARY KEY` | the device's own event id - this is what makes replay idempotent |
| `ts` | `TIMESTAMPTZ NOT NULL` | device clock, offset-aware |
| `system_slug` | `TEXT NOT NULL` | one shared DB across a box's masters |
| `host_serial` | `TEXT` | `__local__` for the box's own detection app (`master_server.py:257`) |
| `device_label` | `TEXT` | human label at ingest time |
| `type` | `TEXT` | `violation` \| `info` \| `alert` \| `system` |
| `severity` | `TEXT` | `ok` \| `warn` \| `crit` |
| `label` | `TEXT` | short human string |
| `snapshot_id` | `TEXT` | resolves against the device's footage endpoint |
| `camera`, `zone`, `track_id`, `person_id`, `object_id`, `detection_label`, `direction`, `model` | `TEXT` | lifted out of `detail` for indexed querying; NULL when a system does not produce them |
| `confidence` | `REAL` | |
| `function` | `TEXT` | added by `0005` - which analytics function raised this |
| `detail` | `JSONB` | the envelope's `detail`, untouched |
| `status` | `TEXT DEFAULT 'open'` | operator workflow: `open` \| `ack` \| `closed` |
| `notes` | `TEXT` | operator notes |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | ingest time, as distinct from `ts` |

Indexes, and the query each one exists for:

| index | columns | serves |
|---|---|---|
| `events_system_ts_idx` | `(system_slug, ts DESC)` | the event table, newest first |
| `events_type_idx` | `(system_slug, type)` | type facet |
| `events_camera_idx` | `(system_slug, camera)` | camera facet |
| `events_zone_idx` | `(system_slug, zone)` | zone facet |
| `events_label_idx` | `(system_slug, detection_label)` | class facet |
| `events_status_idx` | `(system_slug, status)` | the open/ack/closed queue |
| `events_function_idx` | `(system_slug, function)` | "the violations *my* variable raised" |
| `events_ts_idx` | `(ts)` | the retention prune |
| `events_fn_ts_idx` | `(system_slug, function, ts DESC)` | a card scoped to one function over a period |
| `events_type_ts_idx` | `(system_slug, type, ts DESC)` | the event table filtered by type over a period |

The last three are `0007`, and the reasoning behind them is worth keeping. `prune_before` issues
`DELETE FROM events WHERE ts < %s`, which cannot use `events_system_ts_idx` because that index
leads with `system_slug` and the predicate never mentions it - so every sweep, one every six
hours for ever, was a sequential scan of the whole table on a schedule nobody watches. One btree
insert per row buys that back (`0007_growth_indexes.sql:7-19`). Likewise, with only separate
`(system_slug, type)` and `(system_slug, ts)` indexes the planner picks one and filters the rest
by hand, reading far more than it returns once a year of rows has accumulated
(`0007_growth_indexes.sql:37-46`).

`0005` promoted `function` out of `detail` and **backfilled it from the rows already stored**
(`UPDATE events SET function = detail ->> 'function' …`), so history counts the same as new events
rather than starting from empty (`0005_event_function.sql:18-21`).

Written by `core/repos/events.py insert_many()` (`:113`) from the master's two ingest paths -
the slave sync loop (`master_server.py:206-214`) and the local detection app's log drain
(`master_server.py:254-260`) - with `expand()` (`:56`) doing the envelope-to-row widening. Status
and notes are written by `set_status()` (`:437`) from `/api/events`. Read by `query()` (`:225`),
`aggregate()` (`:272`) and `attributes()` (`:326`), which back `core/routes/events.py`, the
Builder's widget queries (`core/services/builder.py:1073-1087`) and the workflow editor's field
picker (`core/routes/workflow.py:233-250`). Pruned by `prune_before()` (`:351`) and removed
wholesale for a deleted system by `delete_for_system()` (`:410`).

### 1.8 `dataset_rows`

Operator-defined tables. A workflow could publish numbers (stat keys) and fire events, but not
record a *row*: "every time someone crosses the line, log the time, camera, zone, track id and
dwell seconds as one record" had nowhere to go, because `events.detail` is not queryable - the
filterable column set on `events` is fixed by the schema.

| column | type | notes |
|---|---|---|
| `event_id` | `TEXT PRIMARY KEY` | the **source event's** id |
| `ts` | `TIMESTAMPTZ NOT NULL` | device clock |
| `system_slug` | `TEXT NOT NULL` | |
| `dataset` | `TEXT NOT NULL` | the Dataset node's key |
| `cols` | `JSONB NOT NULL` | `{column_name: value}`, per the node's declaration |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `deleted_at` | `TIMESTAMPTZ` | added by `0008` - soft delete |
| `edited_at` | `TIMESTAMPTZ` | added by `0008` - a person changed this row |

Two design decisions here are load-bearing, and both come from the log being upstream of the
database.

**Why `event_id` is the primary key and not a serial.** The detection app has no database access,
so a dataset row travels the same road as everything else: appended to `events.jsonl` with
`detail.dataset` set, and expanded on ingest. That log is re-read from offset 0 on every master
boot. Keying on the source event's id makes a replay a no-op instead of a duplicate
(`0006_dataset_rows.sql:12-18`).

**Why a delete is a flag and not a `DELETE`.** Idempotence stops duplicates; it does not stop
resurrection. A row deleted by hand would be re-inserted by the very next replay - the operator
deletes it, sees it go, restarts the box a week later and it is back with no explanation. So the
row stays, the replay's `ON CONFLICT DO NOTHING` leaves the flag alone, and every read filters it
out (`0008_dataset_editing.sql:8-13`). Retention still removes these for real; a soft delete is
about surviving replay, not about keeping data for ever.

**Why a separate table at all.** Dataset writes can be timer-paced - a row every few seconds - so
they are the highest-volume rows on the box. Folding them into `events` would inflate all of its
indexes for rows no event query ever wants (`0006_dataset_rows.sql:20-23`).

Indexes: `dataset_rows_ds_ts_idx` on `(system_slug, dataset, ts DESC) WHERE deleted_at IS NULL` -
partial, because every read filters on `deleted_at` and each page turn would otherwise re-check
rows it will discard (`0008_dataset_editing.sql:24-28`); `dataset_rows_ts_idx (ts)` for the prune;
and `dataset_rows_cols_idx` GIN on `cols jsonb_path_ops`.

That GIN index has a history worth recording. `0006` created it with the default `jsonb_ops`
opclass, reasoning that "grouping and filtering happens on `cols`". That was wrong: `jsonb_ops`
indexes the containment and existence operators (`@>`, `?`, `?|`, `?&`) and nothing else, while
every filter the repo generated was `cols->>'name' = %s`, which no GIN index can serve. It slowed
down every insert and accelerated no read. `0007` did not drop it - instead the repo now emits
`cols @> '{"name":"value"}'::jsonb` for text and datetime equality (`core/repos/datasets.py:190`),
which *is* what the index is for, and the opclass moved to `jsonb_path_ops`: roughly half the size
and faster for exactly that operator (`0007_growth_indexes.sql:21-35`).

Written by `core/repos/datasets.py insert_many()` (`:134`), fanned out from the event insert by
`core/repos/events.py _fan_out_datasets()` (`:150`) - so both ingest paths get dataset rows from
one place - and by the manual editing paths `insert_row` (`:468`, ids are `manual-<uuid>` so they
can never collide with a replayed event), `update_row` (`:500`, `SET cols = cols || %s::jsonb`, a
merge and not a replace) and `delete_row` (`:528`, the soft delete, capped at 500 ids per call).
Read by `query` (`:240`), `latest` (`:295`), `aggregate` (`:345`), `values` (`:439`) and
`table_stats` (`:557`), behind `core/routes/datasets.py` and the Builder's table and card widgets.

### 1.9 The read-only SQL console

`core/services/sqlconsole.py` exposes a per-system query surface that is a *schema of views*, not
a grant on the tables. It creates `q_<slug>.events` and `q_<slug>.dataset_rows` as
`security_barrier` views with `WHERE system_slug = '<slug>'` baked in
(`core/services/sqlconsole.py:186-194`), plus one view per operator-defined table with the JSONB
columns projected out (`:208-211`). Queries run as a separate read-only role whose password is
generated once into `config/storage.local.toml [postgres].ro_password`
(`core/services/sqlconsole.py:88-95`) - the only Postgres key that is machine-written rather than
set by an operator. A system's console therefore cannot read another system's rows even if the
query asks for them.

### 1.10 Planner statistics

Nothing in the product had ever run `ANALYZE`, so after the first large prune the planner's row
estimates were a year stale - which is how a query that was an index scan silently becomes a
sequential one. `0007` runs an initial `ANALYZE events; ANALYZE dataset_rows;`, and the retention
sweep runs one from then on, but only when something was actually deleted
(`core/services/retention.py:383-386`, `core/repos/events.py:386`).

---

## 2. Retention

The policy is **platform** configuration, set by the admin on the hub and never per-system. It
lives under `[retention]` in `config/storage.toml` with overrides in `storage.local.toml`, the
same layering as every other hub setting.

| key | sweeps | max |
|---|---|---|
| `events_days` | per-day event archives under each system's archive dir (`events-*.jsonl.gz`) | 45 |
| `snapshots_days` | evidence JPEGs under each system's `data/snapshots` (`*.jpg`) | 45 |
| `clips_days` | recorded MP4s under each system's `data/clips` (`*.mp4`) | 45 |
| `history_months` | analytics day-files under `<store>/history` (`*.jsonl`, `*.jsonl.gz`) | 12 |
| `events_months` | the Postgres `events` and `dataset_rows` tables | 12 |

The maximums are product policy and are clamped in code (`core/services/retention.py:43-47`); an
admin can shorten a period but not extend it.

**Auth, credentials, the audit log, configs, templates and workflows are never retention-managed.**
That is structural, not a default. The sweep can only delete a file matching an explicit
(directory, pattern, age) allowlist; a `NEVER_DELETE` set of load-bearing names is checked again
immediately before every unlink; and `_DELETABLE_EXTS` refuses any name outside
`.jpg/.jpeg/.mp4/.gz/.jsonl` even when a glob somehow matched it
(`core/services/retention.py:49-62`). A misconfigured policy can at worst delete media and archives
early. It cannot reach a credential file, because no allowlist entry can name one.

The live event log is protected on the same list - `events.jsonl`, `events.jsonl.1`, `events.gen`,
`events.archived`. Retention prunes the *archive*; the live window belongs to the app.

**Schedule.** `SWEEP_INTERVAL_S = 6 * 3600` (`core/services/retention.py:66`). The hub starts a
daemon thread at boot that sleeps 300 seconds to let boot settle, then sweeps every system package
plus the history folder plus the database, for ever (`hub_server.py:126-153`). It is a daemon
thread and never the request path: sweeping is file I/O over every system's snapshots, clips and
archives, and must not block a page. A failed pass logs the traceback and the thread survives -
a sweep that fails silently is how a disk fills up unnoticed. Saving a new policy also kicks one
off in the background immediately (`core/routes/hub.py:800`), so a shortened period takes effect
without waiting up to six hours for the timer.

Ages are read two different ways, and which one applies is not arbitrary
(`core/services/retention.py:274-279`). Archives and history day-files carry their own date in the
filename and are aged by *that*, because the mtime of a copied or restored file lies. Snapshots and
clips are named by a timestamp id but are aged by mtime, which for them is the truth.

`_last_sweep` is process-local and deliberately not persisted
(`core/services/retention.py:68-70`): it describes *this* hub process, and a restart genuinely has
not swept yet. `starts_at`, by contrast, is persisted, because "these numbers have applied since
3 September" is a fact about the deployment, not about this process.

**The retention clock starts when the policy came into force, not when the hub booted.** A 45-day
rule created today has nothing 45 days old under it yet, and deleting on the first pass would
apply a new rule to data recorded under the old one (`core/services/retention.py:87-97`).
Back-dating `starts_at` is the deliberate way to enforce a shortened policy immediately.

History day-files get a middle state before deletion: anything older than 35 days is gzipped in
place (the rings only ever read about 30 days back), which is what makes twelve months affordable
on the eMMC - this data compresses roughly 10:1 (`core/services/retention.py:331-362`).

The database sweep is best-effort and independent: an unreachable Postgres is a skipped cycle
recorded in the report, never an error that stops the file sweep
(`core/services/retention.py:365-389`).

There is deliberately **no "reset all data"** anywhere in the product. The only manual deletion is
`purge_system()` (`core/services/retention.py:535`) - one named system, its media and event data
only, invoked explicitly by an admin, with scopes `all` / `events` / `footage` / `counters`
(`:421`). Configuration, zones, functions and layouts are out of its reach, and `events.gen` /
`events.archived` are kept even by a full purge so the cursor generation contract is not broken
(`:617-620`).

The `counters` scope is worth reading closely, because it is the one place where the log-is-truth
rule bites the operator. Resetting a system's counts means deleting `fn_daily.json`, then
`events_repo.delete_for_system()` and `datasets_repo.delete_for_system()` - and then it *must*
fall through to deleting the live event log too (`:604-607`), because a replay of a log the
database no longer matches would simply restore every row the operator just cleared.

---

## 3. The TOML config files

The other half of persistence, and on a box with no Postgres, effectively all of it.

Every surface follows the same two-file layering (`core/config.py:336-354`): a committed
`<name>.toml` holds structure and safe defaults, a gitignored `<name>.local.toml` holds secrets
and per-device values, and local wins **per key** with sub-tables merged field-by-field - so
overriding one password does not wipe its siblings. `load()` is cached; `load_local()` is
deliberately not, because `save_local()` rewrites the file wholesale and a second cache is a second
thing to forget to invalidate.

A malformed file raises rather than falling back to defaults (`core/config.py:325-328`): a typo
would otherwise boot a deployment with the wrong brand or the wrong database, silently.

Only the local half is ever machine-written. `save_local()` writes to a sibling temp file,
`chmod 0600`, and `os.replace`s it in - atomic, because these files hold accounts and tokens and a
torn users file is a box nobody can log in to (`core/config.py:497-520`).

### 3.1 Platform files under `config/`

| File | Owns | Written by |
|---|---|---|
| `brand.toml` | product name, accent colours, logo, brand sub-line - everything a deployment renames | humans |
| `systems.toml` | `dir`, the `enabled` list, `[hub].url`, `[masters.<slug>].url`, cover art | humans |
| `systems.local.toml` | real master URLs and ports, `autostart` flags, stored system display names | `core/services/provision.py` - `reconcile_masters` (`:1336`), `heal_master_hosts` (`:907`), `rename_system` (`:1448`) |
| `slaves.toml` | one block per device keyed by `host_serial`: `system_slug`, `label`, optional `url`; `[sync]` intervals; `[discovery]` settings | humans |
| `slaves.local.toml` | device bearer tokens (Fernet-encrypted, `enc:` prefix) and discovered URLs | `master_server.py:160-177` on discovery; the pairing routes |
| `storage.toml` | `[postgres]` connection, `[retention]` policy | humans |
| `storage.local.toml` | the Postgres password and the generated `ro_password` for the SQL console role; `[secrets].key`, the Fernet key that encrypts device tokens; retention overrides written from the hub's Administration panel | `core/services/secrets.py`, `core/services/sqlconsole.py:88-95`, the retention routes |
| `auth.toml` | auth `mode`, role names and labels, session idle timeout, pairing unlock window | humans |
| `auth.local.toml` | the admin username, salt and password hash; `[session].secret`, the session signing key | `core/services/auth.py` - `ensure_secret` (`:93`), `set_master_password` (`:160`) |
| `users.local.toml` | operator accounts: per-user salt, password hash, allowed system slugs (`"*"` = all), `enabled`, `developer` | the hub's account routes, via `core/services/auth.py` |
| `sessions.local.toml` | the live session registry, one table per session id: user, coarse device word, IP, login/expiry/seen, revoked | `core/services/auth.py:783-800` |
| `store.toml` | the free and pro store repo coordinates and the store's ed25519 signing **public** key | humans |
| `store.local.toml` | the customer's license key, the `last_seen_time` high-water mark, and an `[installs.<key>]` record per installed template | `core/services/store.py` |
| `licenses.local.toml` | issued license keys - **owner's hub only** | `core/services/store.py:449-478` |
| `update.toml` | the release repo, the OTA signing public key, `[check].interval_hours` | humans |
| `update.local.toml` | the device's read-only GitHub release token (`token`, customer repo) and, on a dev box only, the developer token (`dev_token`, dev release repo) | `core/services/update.py set_token()` (`:114`), and `scripts/install.sh:344-348` |
| `deploy.toml` | `mode = "customer" \| "dev"` - whether this box carries the owner's tooling at all | humans, deliberately with no API or UI that writes it |
| `builtin.toml` | which system packages `scripts/install.sh` copies onto an appliance | humans |
| `monitoring.toml` | health thresholds per telemetry metric; client refresh cadences | humans |
| `workflow.toml` | `[python].enabled` - may a Python Function node's code be written from the browser and run in the detection loop | humans; off by default and deliberately not switchable from the web UI |
| `formats.toml` | the create-a-system format catalogue: overrides, hidden tombstones, categories | `core/services/provision.py save_format()` (`:492`) |
| `workbench.local.toml` | which system is a format's editing workbench, per box | `core/services/provision.py set_format_workbench()` (`:813`) |
| `store_signing.pem` | the owner's ed25519 **private** signing key - never committed, never shipped, removed by the installer if it is found on an appliance (`scripts/install.sh:381-385`) | the owner |

`config/layouts/` appears in `.gitignore` as a legacy location; Builder layouts now live in the
store root (§3.4).

### 3.2 Per-system files

A system package owns its own app config, with the same two-file layering. The dashboard never
reads it - the app and its adapter do (`systems/demo/config.toml:1-6`).

| File | Owns |
|---|---|
| `<pkg>/config.toml` | `[app]` display name, slug, template, `stream_port`, event debounce; `[camera]` source/size/fps, `ffmpeg_threads` (default 2) and `ffmpeg_options` (default "") for FFmpeg-decoded sources; `[video]` `overlay` ("server" default, burned into the frame; "client" = plain frames, the browser draws from the app's `/overlay` events), `overlay_hz` (default 10), `overlay_delay_ms` (default 0), `on_demand` (default false: true pauses a legacy-view camera's paint, encode and publish while it has no WebRTC reader and no MJPEG viewer), `view_on_detect` (default false: true makes live tiles open the video only while the AI detects something; needs `overlay = "client"`) and `view_hold_s` (default 30, 2..120: seconds the video stays open after the last detection); `[detector]` kind, confidence, task, anchor, class filter; `[detector.hailo]` model, models dir, labels; `[detector.gstreamer]` post-process `.so`; `[storage]` `keep_snapshots`, `events_live_days`, `max_events_mb`, `max_log_mb` |
| `<pkg>/config.local.toml` | everything the dashboard writes: `[[cameras]]`, `[[zones]]`, `[[functions]]`, `[[outputs]]`, `[capture]`, `[hud]`, `[tracker]`, `data_dir` and archive overrides |
| `<pkg>/models.toml` | the model catalogue the fetch script reads |
| `<pkg>/system.py` | the system's declaration; a generated one carries the "generated by the IRIV Vision Studio builder" marker that tells provisioning it may be moved to the store |
| `<pkg>/app/store_ext/` | extension code installed from a **signed** store bundle, plus `.manifest_sha256` |

The split is what makes the whole no-code product work. `config.local.toml` is the hot-reload
channel: `core/services/detector_control.py` writes one key at a time and the running app re-reads
its zones, functions and outputs without a restart (`core/services/detector_control.py:8-15`,
`:2524`, `:2538`). It is also why `repair_store_apps()` refreshes only `app/`, `scripts/` and
`models.toml` and never touches config, data, `system.py` or `layout.json`
(`core/services/provision.py:1273-1276`).

Functions and zones are **data, never code**. The expression validator in
`core/services/functions.py` mirrors the app-side whitelist exactly, and the app engine
re-validates on load - so even a bypassed API cannot plant executable code
(`core/services/functions.py:12-17`).

### 3.3 Per-system data directory

`[app].data_dir` - the NVMe (`/mnt/nvme/cytron/<slug>/data`) when the drive is really mounted, the
package's own `./data` on the eMMC otherwise (`systems/demo/app/config.py:172`).

| Path | Holds |
|---|---|
| `events.jsonl` | the live append-only event log. The cursor is `generation * GEN_STRIDE + byte offset`, monotonic and restart-proof because it is a property of the files rather than a counter in memory (`systems/demo/app/store.py:9-21`) |
| `events.jsonl.1` | the previous generation, **kept** so a reader mid-file when rotation happens can finish it - no event skipped, none replayed |
| `events.gen` | the current generation number |
| `events.archived` | the last generation safely archived. A rotation never overwrites an unarchived `.1`; it defers, letting the live file grow, because bounded growth is recoverable and a silently dropped event is not |
| `state.json` | `{pid, started_at, running, camera, detector, error, heartbeat, fps, …}`, rewritten every loop cycle. Liveness has to be a fact on disk, not a variable in memory, or the master could not tell "detection stopped" from "the device vanished" (`systems/demo/app/runtime.py:5-11`) |
| `app.log`, `app.log.1` | the app's stdout/stderr, trimmed to `[storage].max_log_mb` in place while the writer holds the fd (`systems/demo/app/runtime.py:31-56`) |
| `fn_daily.json` | per-day tallies the counting functions keep - what "today / this week / this month" reads. Flushed at most every 10 s, kept 120 days (`systems/demo/app/functions.py:63-69`) |
| `snapshots/*.jpg` | evidence frames, oldest-by-mtime pruned past `[storage].keep_snapshots` (default 500) on every write |
| `clips/*.mp4` | recorded video, at most `MAX_CLIPS = 100` files of at most `MAX_CLIP_S = 5.0` seconds each (`systems/demo/app/capture.py:27-28`) |
| `../archive/events-YYYY-MM-DD.<gen>.jsonl.gz` | rotated events, **moved never deleted**. One file per (day, generation), because `os.replace` of a finished temp file is atomic and idempotent: a power cut mid-archive redoes the whole generation and overwrites the same names (`systems/demo/app/store.py:38-56`) |

Rotation trips on whichever comes first: the oldest live event reaching `events_live_days`
(default 7 - the normal trigger), or the file passing `max_events_mb` (default 64 MB - the
backstop, because age alone cannot protect a box where something writes a million rows in an hour).

### 3.4 The store root

`core/config.py store_dir()` (`:118`), outside the code tree by design.

| Path | Holds | Written by |
|---|---|---|
| `systems/<slug>/` | every operator-created system package | `core/services/provision.py` |
| `layouts/<slug>.json` | Builder dashboard layouts, one file per system | `core/services/builder.py:155`, `:343` |
| `workflows/<slug>.json` | visual pipeline graphs, one per system | `core/services/workflow.py:1341` |
| `history/YYYY-MM-DD.jsonl[.gz]` | sampled stat time series | `core/services/history.py:98-105` |
| `images/` | operator-uploaded system cover images | `core/config.py:215` |
| `templates/` | saved system templates | `core/services/templates.py` |
| `models/` | the shared models fallback when there is no NVMe | `core/services/models.py` |
| `store_cache/catalog.json` | the store catalogue cache | `core/services/store.py:85`, `:491` |
| `update/` | `check.json`, `job.json`, `status.json`, `maintenance`, the staged `apply_update.py`, and `staging/` | `core/services/update.py`, `scripts/apply_update.py` |
| `.cytron_store`, `.store_location` | the marker that keeps a root counting as the store, and the pointer that makes a relocation a detected, printed event | `core/config.py:88-115` |

All of it is written with atomic temp-then-replace. The `update/` subtree in particular must
survive the very swap it is describing, which is why it is on the store volume and not in the
tree.

---

## 4. Secrets

| Secret | Lives in | Protection |
|---|---|---|
| Admin password | `config/auth.local.toml` `[master].password_hash` + `salt` | salted hash; the legacy cleartext `password` key is rewritten as a hash on the first correct login and removed from disk (`core/services/auth.py:160-197`) |
| Session signing secret | `config/auth.local.toml` `[session].secret` | 32 bytes of `secrets.token_urlsafe`, generated on first use under a write lock |
| Operator passwords | `config/users.local.toml` | per-user salt + hash |
| Device bearer tokens | `config/slaves.local.toml` | Fernet-encrypted with an `enc:` prefix; a plaintext legacy value passes through unchanged and is re-saved encrypted on its next write (`core/services/secrets.py:1-16`) |
| Fernet key | `config/storage.local.toml` `[secrets].key` | generated once, masked on read, never logged |
| Postgres password | `config/storage.local.toml` `[postgres].password` | |
| OTA release tokens | `config/update.local.toml` | read-only, each scoped to its own channel's release repo alone (`token` for irivai-release, `dev_token` for irivai-release-dev, which a customer box never holds); never appears in a URL, an argument list, a log line or an API response (`core/services/update.py:27-30`) |
| Store license key | `config/store.local.toml` | carries the customer's read-only GitHub token encrypted against the device serial |
| Store signing private key | `config/store_signing.pem` | owner's hub only; never committed, and deleted by the installer if it is found on an appliance |
| Agent pairing state | `/var/lib/cytron-agent/pairing.json` | directory `700`, file `600`, owned by the service account |

Permissions are set in two places and agree. `core/config.py save_local()` chmods every file it
writes to `0600` (`:514-518`), and `scripts/install.sh` re-asserts it across the whole config
directory at install time: `chmod 755` on `config/` itself - it must be *writable*, because pairing
a device and the first login both create a `*.local.toml` - and `chmod 600` plus
`chown <service-user>` on every `*.local.toml` in it (`scripts/install.sh:373-379`).

Three rules hold everywhere, and `docs/conventions.md` §6-7 is the arbiter: secrets never go in a
committed file; secrets never go in the database; secrets are masked on read
(`core/config.py mask()`, `:523`).

---

## 5. Provisioning a database

Create the role and database, set the password in `config/storage.local.toml [postgres].password`,
then run the migrations:

```bash
sudo -u postgres createuser cytron
sudo -u postgres createdb -O cytron cytron_dashboard
python -m core.db.migrations
```

`scripts/setup_nvme.sh` moves the cluster's data directory onto the NVMe with the cluster stopped,
keeping the original copy as a recoverable backup. The eMMC wears out under the event store's
write traffic; the NVMe is the right home for it, exactly as it is for snapshots
(`scripts/setup_nvme.sh:1-16`).

None of this is required for the box to run. A master boots, polls, streams video, controls
devices and authenticates with no database at all - it just loses folders, the audit log and
queryable history until one is reachable.
