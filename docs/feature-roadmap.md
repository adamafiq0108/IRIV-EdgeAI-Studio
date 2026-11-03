# Feature roadmap - no-code AI-vision builder

Status: captured from product direction discussion, 2026-07-22. Read together
with `docs/commercialization-roadmap.md` (business framing) and
`docs/v2-enhancement-spec.md`. Items are roughly ordered by dependency, not
priority - several build on each other.

The through-line: the hub's "New AI Vision" flow (see
`core/services/provision.py`) grows from "name → generated dashboard" into a
guided system designer, and the created system's pages grow the runtime
features the designer promises.

## 1. Live video feed on the created system's dashboard

A created AI system shows its camera's live feed inside the dashboard (the
Stream page exists on masters; the ask is feed-on-the-dashboard for the
created system, wired automatically when a device pairs).

## 2. Detection-format templates

At creation, the user picks a default AI detection format - e.g. **counting**,
zone violation, presence - and the generated dashboard fits the choice
(counting → in/out tallies, occupancy, per-day chart; violation → violation
tiles, footage strip). `provision.VISION_WIDGETS` becomes one template of
several. "Other templates also" - keep the template list extensible.

## 3. User-defined functions and variables

Users can define their own variables and small functions (derived values:
rates, ratios, shift tallies) that widgets can bind to, alongside the
device-published keys. Needs a safe evaluation model - the builder's
data-not-code rule (`docs/conventions.md`, publish.py docstring) must hold;
no user code exec on the master. A declarative expression format
(key arithmetic, windows) is the likely shape.

## 4. Detection → action rules (GPIO first)

"When a detection happens, do X." A rules surface on the created system:
suggested action is GPIO (the device side runs on Raspberry Pi); enabling GPIO
checks/validates the GPIO port on the device via the control channel
(Contract v1.2 actions are the natural transport). Other actions later
(webhook, email - email service exists).

## 5. MQTT opt-in

A per-system MQTT toggle; when on, expose setup (broker, topic, auth) and
publish detections/stats. Likely device-side publish with master-side config
UI, mirroring how camera settings flow through the control channel.

## 6. Master/slave role choice per system

When creating a system, the user chooses its deployment: **standalone** (the
default - no fleet linking), **master** (head of a group), or **slave**
(attached to a chosen master system).

**Done 2026-07-22:** the wizard's Deployment step records the role in
`systems.local.toml` (`[masters.<slug>] role/master`), and a slave
**duplicates its master system's dashboard at creation** (layout.json is
portable data; a master with no built layout falls back to the chosen
template).

**Remaining:** the data plane - a slave automatically forwarding its stats up
so the master's dashboard aggregates the group. Reads the same config
entries.

## 7. Snap function → data tables

When the snap function is active, footage isn't only a picture grid: a table
view shows the detection data, and the user chooses which attributes
(columns) to display.

## 8. Many tables → PostgreSQL

Users can create multiple tables (per system), and the backing DB is
auto-created in **PostgreSQL** (today the master DB is SQLite -
`docs/master-db.md`; this is a storage-layer decision with migration
implications, not just a feature).

## 9. DB tools - create / manage / maintain

The user controls the DB connection (connection settings surfaced in the
dashboard, not just config files), plus a DB-tools surface that helps them
create, manage and maintain their databases/tables: create table, browse
rows, column management, retention/vacuum-style maintenance. Pairs with 7–8:
the snap-data tables are the first consumer.

## Done

- Show-password toggle on the login page (2026-07-22).
