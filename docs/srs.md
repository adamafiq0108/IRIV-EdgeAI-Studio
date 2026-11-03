# Software Requirements Specification - IRIV Edge AI

Living document - updated as the product changes.

This is the requirements deliverable for the planning phase: what the product must do, who may
do it, and the limits it must hold inside. It is written against the code, not against an
intention - every requirement names the module that satisfies it, so a requirement that drifts
from the implementation is a diff, not an argument.

Where a number appears here (four systems, four cameras, ten frames a second) it is the number a
constant in the code actually holds. Numbers are cited, never rounded for readability: a spec
whose figures are approximately the code's is a spec nobody can test against.

Companion documents: [`contract-v1.md`](contract-v1.md) is the device contract and is normative
for anything crossing the master/agent boundary; [`api.md`](api.md) is the endpoint reference;
[`conventions.md`](conventions.md) is the code rulebook; [`features.md`](features.md) describes
the same product from a developer's first-day point of view.

---

## 1. Purpose

IRIV Edge AI is an edge vision appliance: a Raspberry Pi CM5 carrier with a Hailo-8 class
accelerator, running a web platform that lets a non-programmer build a working computer-vision
system - point a camera, pick a model, draw the areas that matter, wire what happens when
something crosses one, and watch the numbers come out - without writing code and without a cloud
account.

The requirements below exist because that promise has two hard edges. The first is that the box
is small: one CM5 has to hold the whole product, so every capacity figure in §7 is a real
ceiling rather than a licence tier. The second is that the box is often unreachable: a customer
appliance has no shell, no git, and frequently no permanent internet, so anything an operator
might need - updating the platform, recovering a password, replacing a camera - has to be doable
from the web UI on the box itself.

## 2. Scope

In scope:

- The **hub** process (`hub_server.py`): the systems grid, system lifecycle, platform settings,
  accounts, the audit log, the template store client and over-the-air platform updates.
- The **master** process (`master_server.py`), one per system: that system's dashboard, camera
  and model configuration, zones, workflow, dashboards, data tables, events and integrations.
- The **detection app** (`systems/<slug>/app/`): capture, inference, rules, outputs, MJPEG.
- The **device agent** (`agent/`): Contract v1 over an adapter, for systems whose detection runs
  on a separate paired device.

Out of scope, deliberately:

- **TLS and certificates.** A reverse proxy terminates TLS; the master never manages a
  certificate (`core/services/remote.py`).
- **A cloud backend.** The product has no server-side account, no fleet console and no telemetry
  upload. The GitHub-hosted template store and release repo are file transports, not services
  (`core/services/store.py`, `core/services/update.py`).
- **Multi-box clustering.** One hub serves the systems on its own box. The hub explicitly does
  not proxy its masters (`hub_server.py` module docstring).
- **Any customer's deployment.** Branding, secrets, systemd units and site-specific system
  packages belong to a deployment repo (`conventions.md` §1).

## 3. Definitions

| Term | Meaning |
|---|---|
| **System** | One vision application, as a package folder holding `system.py` (its SPEC), `config.toml` and an `app/`. Identified by its **slug**; its display name is separate and renameable. Discovered by `core/services/registry.py` from the repo's `systems/` dir and the persistent generated store. |
| **Master** | The per-system web process (`master_server.py`), one per system, on ports 5110 upward. It serves that system's SPA data, holds its configuration, and polls its devices. |
| **Hub** | The front process (`hub_server.py`, port 5100). Draws one card per system and links out. It never proxies a master - a hub that proxied would hold every master's sessions and streams, and every system's outage would become the hub's. |
| **App** | The detection app: a separate OS process that captures frames, runs inference, evaluates rules and writes events. Started and stopped independently of its master (`core/services/detector_control.py`). |
| **Agent** | A Contract v1 server on port 5101, one per paired device, running as its own process so it survives the app being killed and relaunched (`docs/contract-v1.md` §1). |
| **Zone** | A polygon drawn over a camera view, with rules and actions attached. Stored per system; up to `MAX_ZONE_ACTIONS` (12) actions each (`core/services/detector_control.py`). |
| **Workflow** | A node graph the operator wires on a canvas. It is an authoring format, never an execution format: on save it compiles to `[[functions]]`, `[[outputs]]` and Builder widgets (`core/services/workflow.py`). |
| **Template** | A reusable blueprint - workflow, dashboards, class filter - with the site left out. Cameras and zones are deliberately excluded: an RTSP address and a polygon mean nothing on the next box. An **export** is the other half, carrying the site as well (`core/services/templates.py`). |
| **Store** | The online template store: signed bundles in a public (free) and a private (pro) GitHub repo, unlocked by a license key (`core/services/store.py`). |
| **Appliance vs developer box** | `config/deploy.toml` `mode`. A customer **appliance** defaults to `customer` and ships without `core/dev/` at all; a **developer box** (`mode = "dev"`) mounts the product-authoring tooling. Three independent locks guard it: the code is absent, the mode is wrong, the account is not a developer (`core/deploy.py`). |

---

## 4. Actors and roles

### 4.1 The identity model, as implemented

There are exactly two things that grant anything, and neither of them is the role name.

The first is **`auth_service.is_admin`** (`core/services/auth.py`). It answers true for the
administrator account named in `config/auth.local.toml` and for any account carrying the
developer flag. It is the one place that decides administration, replacing 43 scattered
comparisons against the string `"admin"`.

The second is **the systems allowlist** on a normal account (`users.local.toml`,
`auth_service.allowed`): a comma list of slugs, or `*`. It decides which systems an account can
see and, through `_require_operator`, which systems it can operate.

**Role names grant nothing.** `BUILTIN_ROLES` is `("engineer", "operator", "viewer")` and an
admin may add up to `MAX_CUSTOM_ROLES` (40) more, precisely because a role is a label describing
what kind of user an account is, not a permission set - which is also why adding a custom role
cannot widen anyone's access. An operator and a viewer with the same systems list and the same
category grants can do exactly the same things. What actually varies between two non-admin
accounts is their systems allowlist and their per-system category grants (§4.3).

The **developer** tier sits above admin. It gates the product's own shipped content - built-in
formats, the store, the saved-template library - and it cannot be granted through the web UI at
all: `update_user` refuses to touch the flag, no route sets it, and `_refuse_if_developer`
blocks an admin from resetting, disabling or deleting a developer account. The only way to make
one is to write `developer = true` into `config/users.local.toml` on the box, which needs shell
access. That is what keeps the tier out of reach of a stolen admin session: an attacker who
takes the admin account gets a deployment, not the product.

### 4.2 Capability matrix

Every row assumes an authenticated session; the middleware in `core/routes/auth.py` refuses
everything else before a handler runs.

| Capability | Gate in code | Developer | Admin | Operator / viewer (account with a systems grant) |
|---|---|---|---|---|
| Sign in, change own password and username, see own sessions | session only | yes | yes | yes |
| See a system's card and open its master | `auth_service.allowed` | yes | yes | only granted slugs |
| Start/stop detection, set autostart, toggle capture, start/stop/restart the local master | `hub._require_operator` | yes | yes | yes, on granted slugs |
| Configure cameras, zones, workflow, dashboards, models, integrations on a master | per-module `_require_admin` | yes | yes | no |
| Create, import, export, rename, delete a system | `hub._require_admin` / inline `is_admin` | yes | yes | no |
| Data retention policy, per-system data purge | `hub._require_admin` | yes | yes | no |
| Accounts, roles, sessions of other users | `auth._require_admin` | yes | yes | no |
| Audit log | `audit._require_admin` | yes | yes | no |
| Process log ring | `logs._require_admin` | yes | yes | no |
| Platform OTA update: check, token, apply, roll back | `update._require_admin` | yes | yes | no |
| Template store: browse catalogue, install, license | `hub._require_developer` | yes | **no** | no |
| Author built-in formats, publish to the store, issue license keys | `core/dev/` + `is_developer` + a signing key only the owner's hub holds | yes | no | no |
| Organise the hub grid into folders | `hub._require_developer` | yes | no | no |
| Exempt from the four-system cap | `provision.MAX_SYSTEMS`, `unlimited=` at developer call sites | yes | no | no |

Two rows deserve their reason stated. **The store is developer-only, browsing included**
(`core/routes/store.py`): a customer appliance's admin is not meant to be shopping for product
content, and hiding a page hides the button, not the endpoint. **OTA update is admin-only, not
developer-only** (`core/routes/update.py`): a customer appliance has no developer account, and an
appliance that could not update itself would be an appliance nobody could fix.

### 4.3 Page categories and per-system grants

A master's navigation is built from six categories, keyed for storage in
`core/services/nav.py`:

| Key | Label | Pages |
|---|---|---|
| `overview` | Overview | Dashboard, Analytics, Builder, Footage |
| `ai_config` | AI Configuration | AI Models, Camera Setup, Workflow, Zones |
| `data` | Data | Database |
| `ai_train` | AI Train | Dataset, Dataset Library |
| `integration` | Integration | Blynk IoT |
| `diagnostics` | Diagnostics | Events Logs, System Logs |

The keys are what an account's per-system visibility grant is stored under
(`users.local.toml`, `[users.<name>.sections]`), so they are stable even when a label is
reworded. The frontend groups its rail from the same six
(`frontend/src/components/nav-sections.js`) and `tests/smoke_sections.py` pins the two copies
equal - a category that exists on one side only is either a grant nobody can see or a rail group
nobody can hide.

`auth_service.section_grants` returns `None` - meaning all categories - for the admin, for a
developer, and for any account with no stored restriction; absence of a restriction is the
default, rather than a stored list that would go stale the day a category is added to the
product. A page no category claims stays visible.

Three pages appear only for an administrator (`nav.ADMIN_PAGES`): AI Models, Blynk IoT and
System Logs. The Dashboard Builder is deliberately not in the nav at all - it is reached from
the Dashboard's own edit button, because an editor belongs beside the thing it edits.

---

## 5. Functional requirements

Each requirement states what the product must do; the citation names the module that satisfies
it. Where a rule is load-bearing - where getting it wrong would be a security or data-loss
problem rather than an inconvenience - the reason is stated with it.

### 5.1 System lifecycle

**FR-1** - The platform shall discover every system package on the box by scanning both the
repo's systems directory and a persistent generated store outside the repo, so a system created
through the UI survives a platform update. *(`core/services/registry.py`,
`core/services/provision.py`)*

**FR-2** - An administrator shall be able to create a system from a name and a format template;
the platform shall scaffold the package, grant the creator access to it, apply the template's
workflow and dashboards, and spawn the system's master without a hub restart.
*(`POST /api/hub/systems`, `core/services/provision.py`)*

**FR-3** - An administrator shall be able to export a whole system as one JSON file and import
one back, carrying cameras, zones, workflow, dashboards and class filter. Ports and credentials
shall never travel with an export - an export is meant to be sent to another box, and a port
number or a token that follows it is either wrong there or dangerous there.
*(`core/services/templates.py`)*

**FR-4** - An administrator shall be able to rename a system's display name without changing its
slug, because the slug is identity: zones, history, dashboards and audit rows are all filed
under it. *(`PATCH /api/hub/systems/{slug}`)*

**FR-5** - Deleting a system shall stop its master and its detection app, and shall delete files
from disk only for a tool-generated package. A hand-written package is disabled in configuration
and its files stay - the platform did not write them and must not be the thing that loses them.
*(`DELETE /api/hub/systems/{slug}`, `provision._GENERATED_MARKER`)*

**FR-6** - An operator holding a system shall be able to start, stop and restart that system's
master and detection app, and set autostart. Running a system is not administering the platform:
the person standing in front of it is expected to start it in the morning and restart it when a
camera drops. *(`hub._require_operator`, `POST /api/hub/systems/{slug}/power|detection|autostart`)*

**FR-7** - Systems flagged for autostart shall have their master spawned and their app started
at hub boot, so a board comes up detecting on power-on - except while a platform update is
mid-flight, where the update applier decides when systems come back. *(`hub_server.create_app`,
`update_service.maintenance()`)*

**FR-8** - The hub shall recover from an address change: a box whose masters are recorded under
a hostname that is no longer it shall have those URLs repointed at loopback before anything reads
them, and a discovered system missing its master entry shall be reassigned one.
*(`provision.heal_master_hosts`, `provision.reconcile_masters`)*

### 5.2 Cameras

**FR-9** - A system shall support multiple cameras, each with its own source, resolution, frame
rate and enabled switch, and shall persist the *kind* of each (USB, RTSP, GigE, file, unset)
rather than re-deriving it, because the operator picked it and a source string is a lossy record
of that choice. *(`core/services/detector_control.py`, `CAMERA_KINDS`)*

**FR-10** - The platform shall refuse a camera configuration that exceeds the per-system,
per-box or per-bus running limits, naming which limit is binding. *(§7 NFR-4;
`detector_control.camera_slots`)*

**FR-11** - A camera that has never been pointed at hardware shall be describable as *not set
up* rather than mislabelled, and a system whose cameras are all in that state shall refuse to
start. A generated picture standing in for a camera is a system that looks like it is watching
something. *(`CAMERA_KINDS` `"unset"`, `detector_control.start`)*

**FR-12** - The platform shall be able to enumerate the cameras attached to the box and take a
still from a source for preview, degrading to an honest empty answer where no hardware exists.
*(`detector_control.snapshot`, `open_capture_spec`)*

**FR-13** - An uploaded clip or still shall be usable as a camera source, opened by exactly the
path a live camera is, so zones, functions, tracker and outputs cannot tell the difference. This
is what lets a system be built and demonstrated before the camera is installed, and what makes a
count reproducible - the same footage run twice gives the same number, which no live camera can
offer. *(`core/services/media.py`)*

### 5.3 Detection and models

**FR-14** - Task and backend shall be chosen separately: eight fixed tasks (detection,
classification, segmentation, pose, face, depth, OCR, tracking) over interchangeable backends
(`mock`, `hailo`, `yolo`, `onnx`). Each task has one fixed result shape so the dashboard renders
events generically. *(`app/detector.py`)*

**FR-15** - Backends shall be imported lazily, so a laptop clone of the product never has to
install HailoRT or torch to run. *(`app/detector.py` registry)*

**FR-16** - The platform shall detect the plugged-in Hailo architecture (8 / 8L / 10H) and
resolve a HEF from `models/<arch>/<name>.hef`. A HEF compiled for one architecture will not load
on another, which is why the architecture is detected rather than configured.
*(`systems/demo/app/hailo.py`)*

**FR-17** - An administrator shall be able to add a model without writing code: upload weights
(`.hef`, `.onnx`, `.tflite`, `.pt`), fill a manifest form, paste labels in class-index order, and
deploy. *(`core/services/models.py`, `core/routes/models.py`)*

**FR-18** - Deploying a model to a paired device shall send the manifest plus a pull URL and let
the device fetch the weights itself; a device whose adapter does not serve the `control.detection`
domain shall refuse the deploy. *(`core/routes/models.py`, `contract-v1.md` §11)*

### 5.4 Zones

**FR-19** - An operator with configuration rights shall be able to draw zones over a live camera
view and attach rules and actions to them. Zones are drawn on the Camera Setup page as a tab over
the same feed - one page for aiming the camera and marking what it watches.
*(`detector_control.set_zones`, `frontend/src/pages/Zones.jsx`, `nav.PAGES`)*

**FR-20** - Removing every zone shall mean *no zones*, not a default reappearing. A default that
comes back is a rule the operator did not write and cannot see. *(`systems/demo/config.toml`
`[detector]`)*

**FR-21** - Zone rules shall be evaluated per camera, each camera keeping its own tracker and
timers, with results folded together by a metric-appropriate rule: counts add, severity takes the
worst camera, a distance takes the closest. Summing a nearest-pair distance across cameras would
be meaningless. *(`systems/demo/app/run.py` `_MERGE_MAX` / `_MERGE_MIN`)*

### 5.5 Workflow

**FR-22** - The platform shall provide a visual node editor for the detection pipeline
(detection stream → class filter → zone → analytics → dashboard/output) and shall compile the
saved graph into the artifacts the platform already runs: `[[functions]]` documents, `[[outputs]]`
documents bound to their function, and Builder widgets on a dedicated Workflow page.
*(`core/services/workflow.py`)*

**FR-23** - Compiled artifacts shall remain **data**, never generated code, and the detection app
shall re-validate every document it loads, so a bypassed API cannot plant something executable.
*(`core/services/functions.py`, `systems/demo/app/functions.py`)*

**FR-24** - A function's identity shall be minted once at node creation and shall survive
renames, so stat keys, history series and widget bindings do not break when an operator retitles
a node. *(`workflow.py`, `node.data.fnId`)*

**FR-25** - Compile errors shall not block saving the graph - auto-save must never lose canvas
work - and shall instead block the artifact writes and surface as badges on the offending nodes.
*(`workflow.py`)*

**FR-26** - Compiled outputs and widgets shall carry a `wf_` id prefix, and anything without it
shall be preserved on merge, so an operator's hand-made widgets are not deleted by a recompile.
*(`workflow.py`)*

**FR-27** - Where the platform permits Python in a workflow, that permission shall be a kill
switch that actually kills: turning `[python].enabled` off and restarting shall stop code written
while it was on, rather than waiting for someone to happen to save that workflow.
*(`workflow.enforce_python_policy`, called for every slug at hub boot)*

### 5.6 Dashboards and widgets

**FR-28** - An administrator shall be able to compose dashboards by dragging tiles, charts and
tables onto a canvas, bind each to a live data key, and preview against real fleet data.
*(`core/services/builder.py`, `core/routes/builder.py`)*

**FR-29** - A saved layout shall render on Analytics immediately, and a published layout shall be
downloadable as a system package or installed into the systems directory without overwriting an
existing package unattended. *(`core/services/publish.py`)*

**FR-30** - **A layout is data. Nothing generated shall be executed as code** - a published
`system.py` loads `layout.json` and calls the same shared renderer every other system uses.
*(`core/services/publish.py`)*

**FR-31** - A system package may declare its own widgets with a `data_fn`; one failing widget
shall not take the page down for the others, because a package's `data_fn` is arbitrary code from
outside core. *(`core/services/analytics.py`, `core/system_spec.py`)*

### 5.7 Data and history

**FR-32** - The master shall sample every live stat key on a fixed cadence into bounded rings and
append each sample to a JSONL day-file, so a restart does not blank every chart on the wall.
*(`core/services/history.py`)*

**FR-33** - A system shall be able to declare its own tables as first-class objects, with a
workflow node acting as a *mapping* into a table rather than owning its definition. The registry
type is authoritative for an existing column: when the definition lived inside the node, two
nodes writing one table could disagree about a column's type with the last compile winning, and
deleting a node deleted the definition while its rows stayed in the database, unqueryable.
*(`core/services/datasets.py`)*

**FR-34** - PostgreSQL shall hold only folders, device display metadata and the audit log, and
the master shall never fail to boot or poll because the database is unreachable: read paths
degrade to empty, write paths answer 503, migrations are best-effort, and polling, telemetry, the
control channel and authentication are all database-independent. *(`docs/master-db.md`,
`core/db/`, `core/repos/`)*

**FR-35** - The platform shall enforce an administrator-set retention policy on a slow background
cadence, off the request path. *(§7 NFR-9; `core/services/retention.py`)*

**FR-36** - Retention shall be structurally incapable of reaching credentials, configuration,
templates, workflows or the audit log: the sweep deletes only files matching an explicit
(directory, pattern, age) allowlist, with a deny-list of load-bearing names checked again
immediately before every unlink. A misconfigured policy can at worst delete media early.
*(`core/services/retention.py`)*

**FR-37** - There shall be no "reset all data" control anywhere. The only manual deletion is a
purge of one named system's media and event data, invoked explicitly by an administrator.
*(`retention.purge_system`, `POST /api/hub/systems/{slug}/data/delete`)*

### 5.8 Events and alerts

**FR-38** - Events shall be emitted on **transitions**, not per frame. A persisting condition
writes one entry event, re-announces itself on a heartbeat so downstream liveness does not mistake
"still there" for silence, and writes one clear event carrying the incident duration after a hold
period that stops a single missed frame from splitting an incident in two. Per-frame emission
wrote roughly 480,000 identical rows a day for one parked object. *(`systems/demo/app/store.py`,
`config.toml` `event_heartbeat_s` / `event_clear_hold_s`)*

**FR-39** - Events shall be queryable by time, type and attributes, aggregatable for charts, and
individually annotatable. *(`core/routes/events.py`)*

**FR-40** - Snapshots and clips shall be recordable as evidence alongside an event, bounded in
length. *(`systems/demo/app/capture.py`, `core/services/media.py`)*

**FR-41** - Outputs shall fire on an alert across a fixed set of kinds - GPIO, MQTT, Modbus,
webhook, email, Telegram, snapshot, record - plus scheduled publishers that push live values on a
cadence rather than on an alert. *(`systems/demo/app/outputs.py`)*

**FR-42** - The platform shall be able to send a scheduled email digest covering the day so far,
with the credential held in a gitignored config file and never returned to the browser unmasked.
*(`core/services/email.py`)*

### 5.9 Integrations

**FR-43** - **Blynk.** An operator shall be able to paste a Blynk device token, map stat keys to
virtual pins, and have the master push current values on an interval over Blynk's HTTPS batch
API. Implemented with stdlib `urllib` only - no SDK and no MQTT session to babysit - because the
master is the internet-facing tier and already holds the freshest aggregate stats.
*(`core/services/blynk.py`)*

**FR-44** - Blynk configuration shall live in the persistent store outside the repo, since it
carries a credential: it must never reach git, and a redeploy must not log the device out of its
cloud. *(`blynk.py`, `<store>/blynk.json`)*

**FR-45** - **MQTT.** The detection app shall be able to publish to an MQTT broker both as an
alert output and as a scheduled publisher, with the client library imported lazily and its
absence reported as an actionable message rather than a traceback.
*(`systems/demo/app/outputs.py`)*

**FR-46** - **Hardware I/O.** The app shall support I²C, GPIO, 1-Wire, serial and Modbus
(RTU/TCP), plus the IRIV CM5 board's isolated DI/DO, RS485, OLED, buzzer, button and LEDs. Pin
numbers shall be configuration, never constants: an unconfigured helper answers "not configured"
rather than driving a relay. *(`systems/demo/app/buses.py`, `app/iriv.py`)*

**FR-47** - **Remote access.** An administrator shall be able to record a public URL and check
whether DNS resolves to this host, and to supervise a `cloudflared` or `ngrok` tunnel. The master
shall never install the binary, never use `shell=True`, and shall audit every start and stop -
publishing the master to the internet is exactly the kind of act that must leave a record.
*(`core/services/remote.py`)*

### 5.10 Accounts and authentication

**FR-48** - One credential store shall serve the hub and every master on a host, with a
host-scoped signed-cookie session. *(`core/services/auth.py`)*

**FR-49** - An administrator shall be able to create, edit, disable, delete and reset the
password of non-developer accounts, assign a systems allowlist and per-system category grants, and
add or remove role labels. *(`core/routes/auth.py`, `/api/users`, `/api/roles`)*

**FR-50** - A developer account shall be unmanageable over the web in every direction - no
password reset, no disable, no delete, no role, no flag toggle - because an admin who could reset
the developer's password would simply log in as the developer, and an admin who could disable one
could lock the product's author out of their own build. *(`auth._refuse_if_developer`,
`auth.update_user`)*

**FR-51** - Disabling an account shall switch off everything it could do, including its developer
flag in effect, even though the flag stays in the file. *(`auth.is_developer`)*

**FR-52** - A user shall be able to see and revoke their own sessions, including revoking all
others, and shall be able to change their own username and password. *(`/api/auth/sessions`,
`/api/auth/username`, `/api/auth/password`)*

**FR-53** - Password recovery shall work without email where email is not configured, through a
time-limited reset token bound to a password nonce so a completed reset invalidates outstanding
links. *(`auth.issue_reset_token`, `auth.verify_reset_token`)*

**FR-54** - Destructive and configuration actions shall be recorded in a durable audit log,
filterable by action and user, and best-effort: a database outage returns false and never blocks
the action being audited. *(`core/services/audit.py`, `core/routes/audit.py`)*

### 5.11 Template store

**FR-55** - A box shall be able to list the free and pro template catalogues from GitHub and
install a template through the same import path a hand-uploaded export uses.
*(`core/services/store.py`)*

**FR-56** - Every template folder shall carry a manifest signed with the owner's ed25519 key,
recording a SHA-256 for every other file including HEFs, so one signature covers the whole
bundle; a box shall refuse anything that does not verify. This is what makes the GitHub repos a
dumb transport rather than a trust root. *(`store.py`)*

**FR-57** - Paid access shall be a license key of the form `IRIV1.<payload>.<signature>` whose
payload binds the device serial, tier, an **absolute** expiry and the customer's read-only GitHub
token encrypted against that serial. Activation shall require internet, expiry shall be checked
against server time from GitHub's `Date` header, and the box shall keep a high-water mark of the
latest server time it has seen so winding the clock back cannot revive a dead key. The absolute
expiry is what stops re-entering an old key from restarting the countdown. *(`store.py`)*

**FR-58** - The honest limit shall be stated rather than hidden: a paying customer can extract
their own token from their own key. That is inherent to serverless licensing; the token is
read-only and scoped to the store repo alone, so the blast radius is the paid catalogue and never
the product source. *(`store.py`)*

**FR-59** - The license-issuing routes shall require `config/store_signing.pem`, which lives only
on the owner's hub, is never shipped and never committed - so those routes refuse on every other
box regardless of account. *(`store.py`, `core/dev/`)*

### 5.12 Platform updates (OTA)

**FR-60** - A customer appliance with no shell and no git shall be able to learn that a new
platform version exists and apply it from the web UI. *(`core/services/update.py`,
`core/routes/update.py`)*

**FR-61** - **The process being updated shall not be the process applying the update.** The hub
spawns its masters as children in its own systemd cgroup, so anything the hub starts dies the
moment the update restarts the hub - half-way through swapping the tree, with nothing alive to
notice or roll back. Applying therefore asks systemd to start `irivai-apply.service`, which lives
in its own cgroup and survives the hub it is replacing. *(`update.apply`, `scripts/apply_update.py`)*

**FR-62** - Nothing in the running process shall write to the live tree: verification, staging and
the swap all happen in the applier, and the update service only reads status files and starts
jobs. *(`update.py`)*

**FR-63** - Releases shall be signed bundles verified against an OTA signing key that is
deliberately **not** the store key - an update rewrites the code that verifies everything else, so
it gets a key of its own. *(`update.PUBKEY_B64`)*

**FR-64** - The health gate shall prove the **new process** is serving, not merely that new files
are on disk: `/api/health` reports a version constant frozen at process start, so a live file read
cannot pass the gate before the restart has happened. *(`core/version.py`, `hub_server` health)*

**FR-65** - An administrator shall be able to roll back to the previous tree through the same
applier. *(`POST /api/hub/update/rollback`)*

**FR-66** - Update checking shall start only on a box that has a release token, so a developer
checkout never phones home. *(`update.start_checker`)*

### 5.13 Device agent and Contract v1

**FR-67** - A system whose detection runs on a separate device shall expose it through an agent
serving Contract v1 on port 5101, as its own process. The separation is load-bearing: detection
apps get killed and relaunched by updates, operators and crashes, and the agent must stay up
through all of it so the master can report "detection stopped" rather than the device vanishing.
*(`docs/contract-v1.md` §1)*

**FR-68** - The agent shall be read-only over whatever the system already records, plus a proxy
for its video, plus the opt-in v1.2 control channel and v1.3 telemetry. It shall never own
detection logic. *(`contract-v1.md` §§1, 11, 12)*

**FR-69** - `/api/health` and `/api/agent/info` shall be unauthenticated and every other agent
endpoint bearer-gated, so the master can distinguish "device down" from "token wrong".
*(`contract-v1.md` §2, `conventions.md` §7)*

**FR-70** - Device identity shall be the Pi CPU serial (`host_serial`), and address discovery
shall answer only "where is the device I already know?" - never "who is this?" - because an
address is not an identity and a DHCP lease change must not read as a failure.
*(`contract-v1.md` §5, `core/services/discovery.py`)*

**FR-71** - Pairing shall be accepted only when the device is unpaired or inside an
operator-opened unlock window, and token comparison shall use `hmac.compare_digest`.
*(`conventions.md` §7, `agent/`)*

**FR-72** - The master shall poll each configured device on an interval: health first
(unauthenticated), then summary from the cursor it saved last cycle, so a restart does not
re-ingest history. *(`core/services/sync.py`)*

**FR-73** - Device health shall be classified from telemetry against configurable thresholds, and
**missing telemetry shall read as `unknown`, never `critical`** - an older agent that reports no
metrics must not look like a failing device. *(`core/services/health.py`,
`config/monitoring.toml`)*

**FR-74** - Device credentials shall be rotatable atomically against the agent's v1.3 rotate
endpoints, gated on administration plus a fresh re-authentication, and a token shall be returned
to the browser only after that re-auth is verified. *(`core/services/credentials.py`)*

**FR-75** - The contract shall be executable: `tools/contract_check.py` is the arbiter, and where
the harness and `docs/contract-v1.md` disagree, the document is the bug.
*(`tools/contract_check.py`, `CONTRIBUTING.md`)*

---

## 6. Non-functional requirements

### 6.1 Performance and capacity

**NFR-1 - Systems per box.** At most `MAX_SYSTEMS` = **4** systems may exist on a deployment.
The cap matches what the box can actually run, because a system that exists but can never start
is a card that only ever disappoints. A developer's box is a workshop rather than a deployment,
so template authoring is exempt, and developer-owned systems are excluded from the deployment's
count - otherwise the grid said "6 of 4 systems" over three visible cards.
*(`core/services/provision.py`)*

**NFR-2 - Detection apps running at once.** At most `MAX_RUNNING_APPS` = **4**. Starting a fifth
is refused with a message naming what is already running. *(`detector_control.start`)*

**NFR-3 - Cameras running at once.** At most `MAX_CAMERAS` = **4** *enabled* cameras across every
system on the box together, and `MAX_CAMERAS_PER_SYSTEM` = **4** for one system. The cap is on
enabled cameras, not configured ones: every running camera costs a grab thread, a decode and a
JPEG encode per frame, and past four the Pi's **cores** run out before the accelerator does. A
disabled camera costs nothing, so `MAX_CONFIGURED_CAMERAS` = **16** entries may be kept and
switched between - four night cameras and four day cameras on one box is a normal setup.
*(`core/services/detector_control.py`)*

**NFR-4 - Cameras per bus.** On top of the caps above: USB **2**, GigE **2**, RTSP **4**, file
**4**. A USB or GigE camera hands the box raw frames and the CPU does the whole capture; an IP
camera encodes on its own silicon and sends H.264, so the box only decodes. *(`KIND_LIMITS`)*

**NFR-5 - Frame rates.** A camera is configured between `MIN_CAMERA_FPS` = **10** and **60** fps,
defaulting to 10. Ten is a floor rather than a preference: below roughly that, a moving object
travels far enough between frames that the tracker loses its identity, and every count built on
identity - crossings, dwell, unique visitors - starts lying rather than merely lagging. With four
or fewer cameras sharing the accelerator there is **no cap**: each runs as fast as it can deliver.
Beyond four - only reachable by a hand-written config, never by the dashboard - every camera is
paced at the 10 fps floor. *(`detector_control.MIN_CAMERA_FPS`, `systems/demo/app/run.py`
`_fps_share`)*

**NFR-6 - Streaming.** The MJPEG server accepts per-client `fps` (1–30) and `quality` (20–90) so a
wall of tiles costs less than a single full-rate view, and the raw feed is paced at what the
camera actually delivers rather than at the configured rate. *(`systems/demo/app/run.py`)*

**NFR-7 - Sampling and charts.** Stat history is sampled every `SAMPLE_S` = **10 s**, rolled up
into 5-minute averages kept for **30 days**, with day-files on disk for anything older.
*(`core/services/history.py`)*

**NFR-8 - Other bounds.** Builder allows **24** widgets per page; a zone carries at most **12**
actions; Blynk allows **32** pin mappings on an interval between **1 s and 1 h**; the log ring
holds **2000** records; at most **200** sessions are tracked; at most **40** custom roles;
an uploaded model file is capped at **512 MB**. *(`builder.MAX_WIDGETS`,
`detector_control.MAX_ZONE_ACTIONS`, `blynk.py`, `logs.MAX_RECORDS`, `auth.MAX_SESSIONS`,
`auth.MAX_CUSTOM_ROLES`, `models.MAX_BYTES`)*

**NFR-9 - Retention.** Event archives, snapshots and clips are kept at most **45 days**; the
analytics history day-files and the master's Postgres event store at most **12 months**. The
sweep runs every **6 hours** on a daemon thread, never on the request path - sweeping is file I/O
over every system's media and must not block a page. *(`retention.LIMITS`,
`retention.SWEEP_INTERVAL_S`)*

**NFR-10 - Device polling.** The sync worker polls each configured device once per
`[sync].interval_s` (default 10 minutes), paged from the durable cursor.
*(`core/services/sync.py`, `config/slaves.toml`)*

### 6.2 Availability and restart behaviour

**NFR-11** - The hub shall not depend on its masters and shall not proxy them, so one system's
outage is not the hub's outage. *(`hub_server.py`)*

**NFR-12** - The master shall boot and poll with PostgreSQL unavailable. *(FR-34)*

**NFR-13** - A detection app's lifetime shall be independent of its master's: an app survives a
master restart, and the agent survives the app. *(`detector_control.py`, `contract-v1.md` §1)*

**NFR-14** - Autostart shall bring flagged systems up on power-on, and shall stand down while an
update is applying. *(FR-7)*

**NFR-15** - Known restart-scoped state shall be documented rather than hidden: the log ring is
in memory and a tunnel is supervised by the master process, so both are lost on a restart. The
ring is in memory on purpose - it works with the database down and cannot fill a Pi's disk.
Durable history is the audit log. *(`core/services/logs.py`, `features.md` "Known limits")*

### 6.3 Security

**NFR-16 - Session wall.** Every request outside `OPEN_PREFIXES` requires a valid session;
API paths and the non-`/api` endpoint prefixes (`/video_feed`, `/footage`) get a 401, while any
other path is served the SPA shell so React can render its own login view.
*(`core/routes/auth.py`)*

**NFR-17 - Boundary-anchored prefix matching.** Open-prefix tests match a whole path segment, not
a bare `startswith`. The earlier form would have silently unauthenticated any future route merely
*beginning* with an open prefix - `/api/publicity`, `/api/healthz-admin`, `/loginsessions`.
*(`auth._under`)*

**NFR-18 - CSRF.** Every state-changing request (POST, PUT, PATCH, DELETE) whose `Origin` or
`Referer` names a different host is refused with 403, before anything looks at the session - and
including the open endpoints, since forced logout and flooding the forgot-password mailer are both
cross-site tricks. A request with neither header is allowed: that is a CLI caller, which has no
ambient cookie and is therefore not a CSRF vector. The **port** need not match, because the hub
and every master are one product to the operator. *(`auth._cross_site`)*

**NFR-19 - Session transport.** The session rides an `HttpOnly`, `SameSite=Lax` cookie, `Secure`
over HTTPS, alongside an `X-Session` header for per-tab identity. A header that fails to verify is
a 401 and never falls through to the cookie - falling back would silently turn a stale tab into
the cookie's user, which is the bug the header exists to fix. *(`auth.require`)*

**NFR-20 - 401 semantics.** Session-level 401s carry `X-Session-Required: 1` so the SPA can tell
"expired" from "wrong password"; endpoint-level 401s (bad pairing credentials, failed re-auth)
deliberately lack it and are surfaced to the user instead. *(`auth.require`)*

**NFR-21 - Caching.** Every session-gated response is `Cache-Control: no-store`, so session-only
content does not survive a logout in the browser's back/forward cache. Hashed assets stay
cacheable because they never reach that line. *(`auth.require`)*

**NFR-22 - Password storage.** Passwords are PBKDF2 with a per-account salt; a legacy hash is
upgraded transparently on a successful login. *(`auth._hash`, `auth._upgrade_stored_password`)*

**NFR-23 - Secrets.** Secrets live only in gitignored `*.local.toml` files, never in a committed
file and never in the database, and are masked on read. The release token is kept at mode 0600 and
never appears in a URL, an argument list, a log line or an API response.
*(`conventions.md` §6, `core/services/update.py`)*

**NFR-24 - Signed bundles.** Store templates and platform releases are both ed25519-signed, with
separate keys. *(FR-56, FR-63)*

**NFR-25 - Layering as a security property.** Routes hold no SQL and no business logic; services
import no web framework; repos hold nothing but parameterised SQL. This is enforced by grep in CI,
which is why the Flask→FastAPI port did not edit `core/services/` at all.
*(`conventions.md` §2, `.github/workflows/ci.yml`)*

**NFR-26 - Log redaction.** Secret-shaped assignments (`token=`, `password=`, `bearer=`, …) are
redacted before a line enters the ring, and an unhandled exception returns
`{"ok": false, "error": "internal error - see Logs"}` with the message kept server-side.
*(`core/services/logs.py`, `core/routes/logs.py`)*

**NFR-27 - Defence in depth for the developer tier.** Three independent locks, in the order an
attacker meets them: the code is not on the box, the box is not a dev box, the account is not a
developer. Any one is enough; they are separate so that copying a full checkout onto an appliance,
handing out a developer account, or editing `deploy.toml` each fails on its own. License issuing
needs a fourth thing - the signing key, which exists only on the owner's hub. *(`core/deploy.py`)*

**NFR-28 - Output escaping.** User-controlled values are rendered with escaping; DOM is never
built by concatenating strings into `innerHTML`. *(`conventions.md` §7)*

### 6.4 Offline operation

**NFR-29** - Detection, zones, workflow, dashboards, events, history, outputs and authentication
shall all work with no internet at all. The only features that require it are the template store
(catalogue, install, license activation), OTA update checks and applies, and outbound
integrations. *(`core/services/store.py`, `core/services/update.py`)*

**NFR-30** - License activation shall require internet by design, since expiry is checked against
server time rather than the box's clock. *(FR-57)*

**NFR-31** - A box with no release token shall never contact the release repo, so an offline or
developer install is silent. *(FR-66)*

### 6.5 Storage

**NFR-32** - Event and media data shall be written to the NVMe when the drive is mounted
(`/mnt/nvme/cytron/<slug>/data`) and to the package's local directory on eMMC otherwise, so a box
without the drive still runs rather than failing to start. *(`systems/demo/app/config.py`)*

**NFR-33** - User-generated state - created system packages, Builder layouts, workflow graphs,
templates, history day-files, Blynk config, uploaded models - shall live in a persistent store
**outside** the repo, so a platform update or a `git pull` cannot delete it.
*(`core/config.generated_systems_dir`, `core/services/builder.py`, `core/services/workflow.py`)*

**NFR-34** - Uploaded weights and every `*.local.toml` are outside git and must be backed up
separately. *(`features.md` "Known limits")*

### 6.6 Maintainability

**NFR-35 - The smoke suite is the contract.** `python tests/run_all.py` is the whole test
contract, and it is what CI runs. The tests are end-to-end checks rather than unit tests: each
drives a real service against a temporary store and asserts on the behaviour an operator would
see - a total that stops counting flicker, a card that stays hidden, a route that answers 401.
Each file runs as its own process so one test's monkeypatching cannot leak into the next, and
node checks (`check_*.mjs`) run beside the Python ones because a component used but never imported
renders as a blank page and esbuild compiles it happily. A behaviour worth keeping gets a
`smoke_*.py`; a file nobody globs is a file nobody runs. *(`tests/run_all.py`)*

**NFR-36 - Line ceilings.** Routes 150, services 300, repos 300, agent 300, JS 200, templates 200.
Hitting a ceiling means splitting by domain - never appending anyway, never reformatting until it
fits. The numbers are round because their value is that they are checkable, not that 150 is
objectively correct. *(`conventions.md` §3)*

**NFR-37 - One page, one set of files.** A change to one page never edits another page's files;
shared logic goes in a service both import. *(`conventions.md` §4)*

**NFR-38 - No customer in the product.** No customer name, hostname, IP, subnet, email address,
serial scheme or branding in code, defaults or comments, and no branching on `system_slug` ever -
a feature that needs a slug check is a feature the contract is missing. *(`conventions.md` §1)*

**NFR-39 - The running app is off-limits.** A device's detection app is read-only to everything in
the platform repo: it is live on hardware, and an adapter that edits it turns a dashboard bug into
a stopped production line. *(`conventions.md` §11)*

**NFR-40 - Error contract.** Every error is `{"ok": false, "error": "..."}` with a real status
code, never a 200 carrying a failure, because the master's health logic keys off status codes.
Every request carries an id on the access line, the error line and the `X-Request-Id` header.
*(`conventions.md` §8, `core/middleware.py`, `core/routes/logs.py`)*

### 6.7 Updateability

**NFR-41** - The platform shall be updatable and rollback-able without shell access, with the
applier isolated from the process it replaces and a health gate that proves the new code is
serving. *(FR-60 … FR-65)*

**NFR-42** - Systems created before an update shall be brought forward: their copy of the
reference app is refreshed from the current reference at hub boot, so they run current app code
rather than the code from their creation day. *(`provision.repair_store_apps`)*

**NFR-43** - Version shall be one number for the whole product, read from the `VERSION` file at
the repo root, with the release workflow asserting the git tag matches it. *(`core/version.py`)*

---

## 7. Constraints and assumptions

**C-1 - Single box.** One hub and its masters run on one machine. The hub knows only the URLs in
`config/systems.toml`, and it explicitly does not proxy. Nothing in the product coordinates two
boxes.

**C-2 - Hailo driver and architecture.** Accelerated inference assumes HailoRT and its kernel
driver are installed on the host. The architecture (hailo8 / hailo8l / hailo10h) is detected at
runtime through `hailortcli`, then `lspci`, then sysfs, and a HEF is resolved per architecture -
**a HEF compiled for one architecture will not load on another**. Without the accelerator the
platform still runs: backends are lazily imported and the `mock` detector needs no model at all.

**C-3 - NVMe.** Event data, snapshots and clips assume an NVMe at `/mnt/nvme`. Absent it the app
falls back to eMMC, which works but is a smaller and slower endurance budget - retention
(NFR-9) is what keeps that survivable.

**C-4 - Python 3.11+.** Twice over: `core/config.py` uses standard-library `tomllib`, and the
routes use `str | None` annotations FastAPI resolves at import.

**C-5 - Ports.** Hub 5100, masters 5110 upward, one agent per device on 5101, the detection app's
MJPEG on its own `stream_port` (5102 in the reference system) - never the agent's port.

**C-6 - PostgreSQL is optional.** It holds folders, device metadata and audit only (FR-34).

**C-7 - The frontend is a built bundle.** FastAPI serves `core/static/app/`; a JSX edit is
invisible on the API port until `npm run build`. Development uses Vite on 5173, proxying the API.

**C-8 - Device enrollment is manual.** A device becomes known to its master by an operator
pairing it and recording it in `slaves.toml`. Discovery re-finds a device already listed; it never
adds one.

**C-9 - TLS is somebody else's job.** A proxy in front terminates it; the master manages no
certificates.

**C-10 - Licence.** The repository's `LICENSE` is a placeholder - no rights are granted until one
is chosen.
