# Test cases

Living document - updated as the product changes.

This is the index to the suite. `docs/test-plan.md` says what is being protected
and why the strategy is "tests as contract"; this file says which file protects
which thing. Every row is taken from the test file's own header comment, because
that header is where the reasoning is maintained - a summary written anywhere
else would drift from it within a month.

**Updated 2026-09-11** (Yaya) for the beta push. 204 files now: 188
`tests/smoke_*.py`, 13 checks (`tests/check_*.py`, `tests/check_*.mjs`), 3
`tests/test_*.mjs`. Current run: `python tests/run_all.py` -> **203/204
passed, 1 skipped**. They are grouped below by area for reading; the runner
does not know about the groups.

**Same day, Workflow node pass (Yaya):** every one of the 94 node types in
`wf.REGISTRY` was linked into a real graph and compiled. 9 had never been
tested at all (see `smoke_nodecoverage.py` above); all 9 now pass, no real
defects found. Full report: `docs/team/yaya/Workflow Node Test Report.docx`.

The grouped catalogue below predates a large batch of tests added since (the
chip-sharing, line-counter and video-throughput fixes shipped this week among
them). Rather than let those 90 files sit uncatalogued, [Appendix: added
since the last full catalogue pass](#appendix-added-since-the-last-full-catalogue-pass)
lists every one, each row pulled straight from that file's own header
comment - the same source this document has always cited.

## Workflow compiler and the function engine

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_common.py` | A Link out/in jump compiles exactly like the wire it stands for; Comment and Debug never reach the engine. | Authoring conveniences leaking into the runtime, or a link jump compiling differently from the wire it replaced. |
| `smoke_palette.py` | Every Function node is filed under a sub-group; the Debug node is retired; a Variable can declare which sides it offers. | A palette whose shape drifts from what the compiler accepts. |
| `check_palette_colors.mjs` | Every workflow category has a colour, and the colours group by stage (hue = stage, shade = neighbouring category). | A category coloured at random, which looks fine alone and silently costs the palette its scannability. |
| `smoke_formula.py` | The Formula node: pick an operation, wire operands in or type numbers. | Operand wiring or literal entry regressing. |
| `smoke_formulachain.py` | Chainable operations take as many inputs as asked for - "a or b or c or d" is one node, not a tree; fixed-arity ops and existing two-input nodes are untouched. | A migration to n-ary operands rewiring graphs already saved. |
| `smoke_mathlib.py` | Every operation the picker offers compiles through the core validator *and* evaluates in the app engine, and none can crash the detection loop whatever it is fed. | A palette entry that validates but cannot run, or an operation that kills the detection loop on bad input. |
| `smoke_total.py` | Running Total: the accumulator the "total = total + new" graphs were reaching for, compile and engine behaviour, including the user's exact case. | Operators hand-building feedback loops for an accumulator that should be one node. |
| `smoke_feedback.py` | A Formula(add) reading the variable its own output is stored into compiles *with* a warning naming the Running Total fix; a legitimate Running Total in the same graph stays clean. | The "hallucinating total" graph compiling silently, and a warning so broad it fires on correct graphs. |
| `smoke_varref.py` | Variable node: pick a value, wire it into a formula, refer to it by name. | Named-value references breaking. |
| `smoke_createvar.py` | A declared variable's constant face - number, boolean or text - compiles to a real hidden `variable.number` node; number and boolean publish a stat, text deliberately publishes nothing. | A text constant reaching an expression engine whose constant whitelist accepts only numbers and booleans. |
| `smoke_switch.py` | Switch branches are exclusive (first match wins) and each output binds to the branch it leaves by. | Two branches firing, or an output bound to the wrong branch. |
| `smoke_switchshapes.py` | The five Switch shapes (compare / switch / range / boolean / multi) and what each one's ports mean; branch ids stay `<base>_b<index>` in row order. | A card or output bound to a branch losing its binding when rows are added. |
| `smoke_switchinput.py` | Switch routes on any wired value via `input_alias`, not only a value that compiled to an `fn_meta` entry. | Wiring a Variable into a Switch failing with "needs exactly one value wired in (has 0)" when a value plainly was. |
| `smoke_condswitch.py` | Zone Functionality is absorbed as a mode of the Zone Counter and migrates in place to the *same* `zone_count` function under the *same* fn id; Switch gains `ctype` shapes whose absence means the old behaviour with the old ids. | A node merge re-binding every output on canvases already saved. |
| `smoke_logictable.py` | The Logic Table compiles rows of "when the inputs look like this, the answer is that" to the same whitelisted expression everything else uses. | A second, less-guarded expression path. |
| `smoke_delay.py` | Outputs after a Delay fire late; delays in a row add up. | Delay collapsing or not composing. |
| `smoke_capture.py` | Snapshot/Record nodes read as unwired when their System Control switch is off, and come back when it is on; the operator's wire is never touched. | A capture switch destroying the wiring it was only meant to gate. |
| `smoke_objectdetect.py` | "Is this object here?" - one compiled `object_detect` rule behind two palette nodes, in a zone or anywhere on camera. | Presence needing a threshold and a filter to express. |
| `smoke_orphan.py` | Deleting a rule deletes only its wire to the Detection Stream, not its canvas nodes; a re-sync does not shuffle nodes the operator moved. | Losing hand-placed layout, and nodes vanishing with the rule they served. |
| `smoke_nowipe.py` | Saving a workflow does not delete zone-action functions that have no node on the canvas yet. | A save wiping behaviour the canvas had not drawn. |
| `smoke_mqtt.py` | MQTT in compiles like an API value, and the engine holds the last message without ever blocking the detection loop. | A broker stall becoming a detection stall. |
| `smoke_boardio.py` | The IRIV Edge AI terminal block reachable from a workflow (DI0, DO0/DO1, L0/L1, PLC register over RS485); the pin map is pinned, applied only to a board identified as an IRIV, and the app-side and core-side copies agree. | A wrong pin number closing a relay on real machinery. |
| `smoke_portspanel.py` | The Ports panel's layout matches the real silkscreen, it reads live state rather than inventing it, and it stops polling when closed. | A panel that answers "does anything happen at the terminal?" with a guess. |
| `smoke_python.py` | Python node plus Catch node: both gates hold, code reaches the box, the runtime runs it, and a crash is contained and catchable. | Browser-authored code escaping its gate or taking the loop down. |
| `smoke_pygate.py` | Only code the editor wrote runs; edits are caught; turning `[python].enabled` off actually removes it. | A disabled feature that is still executing, and hand-edited code running unnoticed. |
| `smoke_ext.py` | A custom function handler wired through the app's FunctionEngine in a system a store install just created; the test performs the install itself rather than depending on test order. | An extension runtime that works only in the developer's tree - and a suite whose result changes with the order it runs in. |

## Counting, tracking and analytics

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_counters.py` | Frame Counter and Zone Counter answer "over what span" with the same control, both count each object once by tracking id, and both hand on the PID they already know. | Two counters with different controls for one question, and a counted-objects table having to source ids from somewhere else. |
| `smoke_framecount.py` | Whole-frame counters: live count, unique-by-PID total, per-day tallies that survive a restart, and Analytics' date range applied to them. | Totals resetting on a bounce, or a date range that does not reach the counters. |
| `smoke_framemerge.py` | One "In Frame" node with a mode, not two nodes; the palette offers one obvious answer for time (Time in Zone) and one for A-to-B (Travel Time). | A palette with two near-identical entries for one question. |
| `smoke_counterreset.py` | "Reset counters" zeroes the dashboard's numbers without taking the footage and event log an operator may still need. | A reset that is really a wipe. |
| `smoke_resetjob.py` | The counters reset runs in the background: the button answers at once, the status names the current step, and a page opening mid-reset can tell. | A request that hangs, and a UI that cannot tell a reset in progress from a finished one. |
| `smoke_tracker.py` | Hailo-named tracker knobs plus confirmation: a one-frame ghost never mints a PID, a real object keeps one id, and settings round-trip the API names. | Ghost detections inflating unique counts, and settings renamed on the way through. |
| `smoke_trails.py` | Motion trails: `analytics.trails` chooses which classes leave one and for how long; a point is stored only when the object *moved*; trails are drawn under everything else. | Dot spacing that no longer means movement, and trails obscuring the detections. |
| `smoke_cyclemodes.py` | Cycle Time's five modes (countdown / dwell / timer / stamps / occupancy) are shared by the Zones page action and the Workflow node; existing configs compile to byte-identical documents. | A mode merge changing what already-deployed systems produce. |
| `smoke_dwell_pid.py` | Time in Zone with PID stamps every *completed* stay as an event carrying PID, seconds and entry/exit times, so a Data Table can write one row per stay with no Alert. | Aggregates (current/live/avg/max) being the only answer, when the question is "who". |
| `smoke_boxcolor.py` | An Alert may set the colour its objects are drawn in (red by default), reaching zone members for a zone rule and class-matched detections for a rule with no zone. | A frame-wide violation ("more than 3 people in frame") that cannot colour anything because it names no zone. |
| `smoke_changes_agg.py` | "How many times it changed" is its own aggregation, and the SQL window query (`datasets_repo.aggregate`) and the in-memory fallback (`builder._agg_value`) agree on it. | `count()` scoring a repeated value and `unique()` scoring a return visit - and a card whose answer depends on whether Postgres was up. |

## Zones

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_zoneflags.py` | A zone's `violation` tick produces a rule and therefore a node; `enabled` off cuts the wire to the Detection Stream. | A tick box with no visible consequence on the canvas. |
| `smoke_zonesync.py` | A zone action mirrors onto the canvas, wires to the Detection Stream, and compiles back to the *same* function ids. | The canvas and the zone page becoming two sources of truth. |
| `smoke_zonetrigger.py` | Ticking a zone's Trigger builds the whole chain - Detection Stream → Zone → Zone Counter → Alert - not a bare counter. | The one thing the operator ticks it *for* having no node to configure (box colour, snapshot, card binding). |
| `smoke_zonealert.py` | Ticking "Raise an alert" / save a photo / record a clip on the Zones page draws those nodes on the canvas. | The page that shows how the system works showing a chain that appears to do nothing. |
| `smoke_zoneviolation.py` | A Violation action builds **one** chain ending in the card that shows the verdict, measuring presence *now* rather than `new`. | Two chains and two alerts per incident, and a Verdict card bound to a value true for one frame per offender. |
| `smoke_zonepid.py` | A zone counter's PID column records who is *in the zone*: the pid port carries the rule's zone, the engine stamps membership, and the row builder refuses an id that is not inside. | `zone_pid` filling with the same ids as `frame_pid` for people who never entered. |

## Cameras, detection and models

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_nocamera.py` | A system nobody has pointed at hardware does not run; a dead camera stays dead and says so while the reconnect ladder keeps trying the real device. | A system that looks like it is watching something while watching a generated picture - including after a real camera dies. |
| `smoke_synthetic.py` | The camera fallback hands the detector something shaped like a camera frame. | A PIL Image with no `.shape` making every inference raise, demoting a healthy Hailo to the `none` detector, and leaving detection dead after the camera comes back. |
| `smoke_camslots.py` | A numeric camera source resolves through the `/dev/cam-usb<n>` udev symlink pinned to the physical socket when it exists, and behaves as before when it does not. | "Camera 0" wandering between sockets across replugs and enumeration order. |
| `smoke_boardports.py` | The faceplate USB and ethernet panels expose live state through one payload (`/api/board/ports`), and the camera-setup row is standardised as picker + icon rescan + icon Ports. | Three different shapes for the same control on one page. |
| `smoke_snapshotshare.py` | A "Preview snapshot" of a camera another app already owns comes from that app's shared `/raw` feed. | "Cannot open camera" every time a second app previews a device the owner holds. |
| `smoke_media.py` | Footage played as a camera: naming, containment, and source encoding - a browser-supplied filename is never allowed to name somewhere else. | Path traversal from a clip filename. |
| `smoke_webrtc.py` | The app publishes to the gateway and the master hands browsers the stream under the same path; the rule is written twice (the app cannot import `core/`) and the copies are pinned equal. | A camera publishing to one address while every viewer asks for another - which looks exactly like "the stream never comes up". |
| `smoke_hudrows.py` | The live feed's four status rows are stamped into the JPEG by the detection app and switched off in config, not by the page; an absent switch means shown. | A row toggle that only works on one viewer, and a system configured before the switches existed changing appearance. |
| `smoke_runstate.py` | "Detection is stopped" means the process is gone, not that it is busy - a stale heartbeat during a slow HEF load or an RTSP reopen is not death. | A "Start detection" card in front of an operator whose detection is running, inviting a second app. |
| `smoke_straypid.py` | The pid file is bookkeeping and `/proc` is the truth: `start()` scans for this package's own app and adopts a survivor instead of racing it. | A twin that dies binding the stream port, overwrites `state.json` with its own pid, and leaves the real app streaming invisibly beyond any Stop. |
| `smoke_masterreap.py` | `spawn_master()` reaps its own child. | One permanent zombie per master bounce - power route, counters-reset, boot - growing the process table forever. |
| `smoke_modeldrop.py` | The drop-folder model store resolves the same HEF filename in several folders by preferring the folder named like the model; id lookup stays exact. | "No detection model named X" for a model that is present twice - an ambiguity is not an absence. |
| `smoke_modeldelete.py` | Deleting a dropped model touches only files inside a model folder, never the running model, and never anything the caller merely names. | Arbitrary file deletion, and deleting the model currently in use. |
| `smoke_mixedmodels.py` | A model dropped into the model folder runs alongside a built-in one - the resolver knows the drop folder exists. | "Add onto this system" failing for a model the page is listing two cards away. |
| `smoke_permodelparams.py` | Each `[[detector.models]]` entry carries its own classes and confidence. | Ticking a class on a two-class board model silencing the COCO model beside it. |

## Dashboard, Analytics and the Builder

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_cardmerge.py` | Six dashboard nodes become three, and canvases saved against the old types compile to exactly what they did before. | A node merge changing every dashboard already drawn. |
| `smoke_cardname.py` | A dashboard node can be named, and naming it publishes it to the editors' "From workflow" rail; an unnamed card keeps the old title and stays off the rail. | Three cards all called "Bay" from one function, a composed card unreachable from the editors, and a palette flooded with identical "Card" rows. |
| `smoke_cardwires.py` | A renamed table column does not freeze the dashboard (the new wire wins with a warning; an all-stale card waits switched off), and a chart takes several wired values as several series while a tile insists on one. | One renamed column erroring `apply_artifacts` for the whole canvas, so no card updates again. |
| `smoke_cardwindow.py` | A card's period (live / follow Analytics / today / week / month) selects which published key it binds to; the card stays an ordinary widget. | A period implemented as special-case maths instead of a different metric. |
| `smoke_chartrange.py` | The full nine-span range menu, a day-aligned "today", and "follow the Analytics range" resolved at render. | A rolling 24h dragging in yesterday evening under the label "today", and a stored widget freezing the page's date choice. |
| `smoke_accents.py` | A compiled `wf_*` card's accent colour survives the next compile, through the same override map `template` and background already had. | The Analytics Builder's accent picker writing into a row the merge throws away. |
| `smoke_builderhide.py` | Hidden and placement settings apply to builder (`wf_*`) rows on the Analytics overview, and `hidden_catalogue` offers hidden cards back. | A hidden card that cannot be un-hidden. |
| `smoke_catalog_origin.py` | Every catalogue entry the canvas publishes carries an `origin`, and a variable is offered. | One undifferentiated "From workflow" column growing with the pipeline until it pushes the widget palette off screen. |
| `check_widget_render.mjs` | Every widget kind actually draws - mounted in jsdom with `react-dom/client` so effects run. | A recharts chart that SSRs to an empty wrapper being reported as "rendered fine" while drawing nothing. |
| `check_daterange.mjs` | The date-range picker's hand-rolled arithmetic: month stepping from a 31st, and local vs UTC days. | Paging forward from Jan 31 skipping February, and an early-morning "today" behind UTC returning yesterday's empty filter. |
| `test_statlabel.mjs` | `statLabel` turns raw stat keys into plain names. | Raw keys leaking into the workflow UI - and a test hardcoded to one laptop's path that never actually ran. |

## Datasets, database and queries

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_dataset_compile.py` | A Dataset node's columns resolve at compile time - a wired column becomes the stat key it was dragged from - so the runtime never sees the graph. | A row recorder that needs the authoring graph at runtime. |
| `smoke_dataset_runtime.py` | Assembling one row, and the three ways a row is written: on alert, on a timer, on change. | A row whose contents depend on anything but a dict lookup, and therefore is not reproducible weeks later. |
| `smoke_dataset_registry.py` | A table is an object and a node is a mapping into it; the table is the source of truth. | Two nodes disagreeing about a column's type with the last compile winning, and deleting a node stranding its rows unqueryable. |
| `smoke_dataset_ports.py` | Every column of a Database Entity node has two ports - write in, read back out - so a table is a source as well as a sink. | A card able to show only what is live, never what was recorded. |
| `smoke_dataset_query.py` | A dataset column name must match `COL_RE` *and* appear in the operator's own declaration, because a column name cannot be bound as a parameter. | SQL injection through a column name, where no constant whitelist exists to check against. |
| `smoke_dataset_edit.py` | Hand edits survive the replay: rows arrive through `events.jsonl`, which the master replays from offset 0 on every boot, so a delete cannot be a plain `DELETE`. | A deleted row resurrecting itself at the next restart and being found a week later with no explanation. |
| `smoke_dataset_live_pid.py` | A Data Table records which objects it counted, one row each - `every`/`change` rows are not event-caused, and a row holds one identity. | A table logging live counting showing "1.0" with a blank pid, or naming one of three people in frame and hiding the rest. |
| `smoke_dataset_reset.py` | Resetting a system's counters clears its datasets too. | Rows no replay can restore (the log is gone) and no reset can reach - a table quietly outliving its own history. |
| `smoke_querynode.py` | A Query reads only tables reached through its *wires*, not names in its text; only SELECT compiles; results are shaped positionally per card kind. | A statement naming an unwired table failing as a database error later instead of at compile, and a hand-written query with no schema to introspect. |
| `smoke_sqlconsole.py` | Raw SQL is scoped inside Postgres: a login role with no rights on any base table, per-system schemas of owner-filtered views, `search_path` set to that schema alone, read-only transaction. | `SELECT * FROM events` returning every system on the box, plus the audit log and the device table. |
| `smoke_dbscale.py` | Cheap structural invariants standing in for a Pi with a year of events in Postgres. | Decisions whose symptom appears months later, when nobody connects the slow dashboard to the line of SQL that caused it. |

## Authentication, sessions and access scope

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_security.py` | The baseline end to end: password hashing and migration, the login throttle, the session wall including non-`/api` routes, CSRF origin refusal, cookie flags. | Any one of those quietly regressing - the non-`/api` routes used to fall straight through. |
| `smoke_sessions.py` | A session is a listable, revocable thing: the token stays self-proving (HMAC first, no database near the login path), and the registry row is what makes it killable; legacy 3-part tokens keep verifying until they expire. | Revocation that can only be waited out, and a deploy that logs everybody out. |
| `smoke_sessionlock.py` | Session renewal is an explicit act behind one endpoint, called only while an operator holds a page's Lock switch on. | A dashboard or live wall signing itself out mid-shift - and the easy wrong fix, where any background poll holds every session open forever. |
| `smoke_pertab.py` | Each tab keeps its own token in `sessionStorage` and sends it as `X-Session`, trusted before the cookie; the cookie remains the identity for `<img>` streams and `<a>` downloads. | A second login silently turning every other tab into the new user. |
| `smoke_authconcurrency.py` | `save_local` writes temp+rename, read-modify-write cycles do not lose writes, and the middleware does not re-read two TOML files per request. | A crash mid-write truncating the users file into a box nobody can log in to; two admins losing one another's edits; ten users queueing on each other's I/O. |
| `smoke_adminname.py` | The administrator is a stored name defaulting to `admin`: every check follows a rename in both directions, the name cannot collide with an operator account, and the administrator appears as a row. | A deployment permanently stuck on the shipped name, a session cookie that cannot tell two identities apart, and an accounts page listing zero accounts on a box somebody is signed into. |
| `smoke_rolesdev.py` | Every role is removable (a role grants nothing - access is the systems allowlist plus the admin account), and the developer account is invisible to everyone else. | A site stuck displaying three built-in roles it does not use, and admins seeing an account they can touch nothing on. |
| `smoke_sections.py` | Per-system nav-category grants: stored in `users.local.toml [users.<name>.sections]`, enforced by the master's `/api/bootstrap` building a filtered nav server-side, with the SPA bouncing typed URLs. | Hiding a rail item and leaving the page reachable, and the two copies of the six categories drifting apart. |
| `smoke_opscope.py` | Running a system is not administering the platform: start/stop/restart/autostart/capture are open to whoever was granted the system; create, rename, import, export and data deletion stay admin-only. | An operator in front of a stopped line unable to start it - and, opening that up, the other half accidentally opening too. |
| `smoke_hubscope.py` | One predicate, `hub.visible_to`, scopes every hub surface. | An admin's Live Preview picker listing the developer's workbench systems that the grid next door is hiding. |
| `smoke_devscope.py` | The workshop and the deployment are separate both ways: a developer sees their workbenches, an admin sees the deployment's systems, and neither set leaks. | Either party's systems appearing in the other's hub. |
| `smoke_auditpage.py` | The activity log pages up to the server's own ceiling of 1000 with the clamp in the same place it always was; the account page's system chips scope on `hub.visible_to`. | A year of audit readable only for its most recent day, and an admin granting access to a system that is not theirs. |
| `smoke_storegate.py` | Every template-store route is developer-only, on the server. | Hiding the nav item and leaving the URL answering - browsing was open to any signed-in user and installing was merely admin. |

## Store, templates and OTA updates

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_store.py` | The store backend against the live public repo: publish and install a signed bundle. | A distribution channel that only works in the developer's tree. Skips (exit 77) where the owner's signing key is absent. |
| `smoke_catalog_origin.py` | (see Dashboard) | |
| `smoke_templatesync.py` | The "Built-in templates" folder *is* the publish switch: dropping a system in snapshots the whole build and publishes it; dragging it out drafts it, content kept; dropping it back republishes the current build. | A system sitting under "Built-in templates" while the format of the same name ships empty - a label and a class list, no workflow, no dashboard. |
| `smoke_templaterefresh.py` | Creating a system from a built-in format re-snapshots the format's linked workbench at create time. | Every new system minted from the snapshot taken the day the format was first published, behind an edited workbench. |
| `smoke_fullclone.py` | A template is the whole build minus recorded data - zones (minus camera binding), capture switches, status-box rows, ids preserved so Workflow zone bindings survive - and it round-trips through `formats.toml`; a compiled card is never auto-placed. | A clone that does not run until rewired, and the compiler placing cards a person did not put there. |
| `smoke_sysgroups.py` | A folder is display metadata: editing the filing never changes what is filed - no port, slug, package or access moves. | A flat list of workbench systems tellable apart only by memory, made worse by a rename that breaks one. |
| `smoke_update.py` | The OTA guards, not the happy path: numeric version compare, a signature checked *before* the JSON it covers is parsed, a tarball that cannot write outside its destination or hand a box a `deploy.toml`, an `apply()` that installs only the version the admin was shown, a "running" flag a dead job cannot wedge, a token that never leaves the box, an applier that survives the tree it replaces, and a sudoers rule granting exactly three commands. | Every failure in this path is unrecoverable in the field. A string version compare alone refuses real updates. |
| `smoke_cardpower.py` | The hub card's power button moves the whole system - master and detection app together. | Stopping only the master, leaving the app running headless holding the camera and the Hailo chip and reviving itself, while the card reads offline. |

## Retention and storage

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `smoke_retentionstamp.py` | "In effect since" is stamped when a policy is *saved*, persists with it, reads as "product default" where nobody has changed it, and cannot be set by a request. | Filling the column with the install date or with "now" - both lies - and back-dating a rule to claim it had been running for months. |
| `smoke_counterreset.py` | (see Counting) | |
| `smoke_dataset_reset.py` | (see Datasets) | |

## Frontend static and render checks

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `check_jsx_refs.mjs` | Every JSX component a file renders is imported or defined in it - component references only, no false positives. | A blank page and "X is not defined" weeks later on a branch nobody renders often. Vite compiles it happily and there is no ESLint in this project. |
| `check_primitives_render.mjs` | Shared UI primitives render with the props the app actually passes, mounted with the real lucide icons. | `EmptyState` shipping broken because a lucide icon is a `forwardRef` object, not a function - React error #31, a blank Footage page, a minified stack. |
| `check_activity.mjs` | An audit entry's `detail` never reaches React as an object. | A JSONB `{}` - truthy - passed straight to React, taking the whole Account Management page down with "objects are not valid as a React child" on every row. |
| `check_widget_render.mjs` | (see Dashboard) | |
| `check_palette_colors.mjs` | (see Workflow) | |
| `check_daterange.mjs` | (see Dashboard) | |
| `check_zonesave.mjs` | What the Zones page SENDS when a line's Crossing threshold is typed in -- the real panel, driven key by key against a recorded server. | "After entering the threshold and save, it didnt save it", reported from a live board. The board stores nothing when the value is the factory 1.2, so the box went blank after a save that had worked perfectly, and a working save was indistinguishable from a lost one. |
| `test_statlabel.mjs` | (see Dashboard) | |

## Whole-tree and cross-copy checks

| Test file | Invariant it pins | Failure it prevents |
| --- | --- | --- |
| `check_undefined_names.py` | A module may not call a private (`_`-prefixed) name it does not have. | "Create a new system" 500ing on every box because `core/routes/hub.py` called `_apply_format_workflow` without importing it - not a syntax error, not an import error, and caught by nothing else. |
| `smoke_sections.py` | (see Auth) - the six categories exist identically in `core/services/nav.py` and `nav-sections.js`. | |
| `smoke_boardio.py` | (see Workflow) - the app-side and core-side pin maps agree. | |
| `smoke_webrtc.py` | (see Cameras) - the app and the master name the same stream path. | |

## How to run it

```bash
python tests/run_all.py            # everything
python tests/run_all.py zone       # only files whose name contains "zone"
```

From the repository root. Each file runs as its own process, so one test's
monkeypatching or module-level state cannot leak into the next. About forty
seconds for the whole suite - a deliberate budget, because a slow gate is a gate
people learn to skip.

**Discovery is a glob, not a registry.** `run_all.py` collects
`smoke_*.py`, `check_*.py`, `check_*.mjs` and `test_*.mjs` from `tests/` and
sorts them. There is nothing to register: a new file matching one of those
patterns runs on the next invocation. The Node checks are globbed alongside the
Python ones on purpose - `check_jsx_refs` catches a class of bug the frontend
build cannot, and a whole-tree check that nothing globs is a check nobody runs,
which is how it would come to pass every time.

Exit code **77** means *skipped*: the precondition does not exist on this
machine. The store tests use it because publishing a signed bundle needs the
owner's signing key, which a customer box and CI correctly do not have. Skips
are printed with their reason and counted in the summary, never swallowed - a
skip nobody sees is how coverage quietly disappears. A missing `node` binary is
reported the same way.

A failure prints the last twelve lines of the test's combined output and the run
exits non-zero.

## The rule

**A new defect gets a test before its fix.** Not after, and not "if it seems
worth it". The test is written at the defect, against the behaviour as the
operator saw it, and its header names the failure it prevents from returning -
which is what makes this file possible to write from the code rather than from
memory. A fix without a test is a fix that will be cleaned up back into the bug
it replaced.

This is item 6 of the definition of done in `docs/test-plan.md`, and it is why
the suite reads as a list of past failures rather than a coverage map. It traces
backwards from defects, not forwards from requirements - see the V-model row in
`docs/sdlc.md`.

---

## Appendix: added since the last full catalogue pass

| Test file | What its header says | Context |
| --- | --- | --- |
| `smoke_apploop.py` | The detection loop must actually RUN once, in a test. | Every other test here checks a piece: the engine's numbers, the compiler's output, the stream server's clients. Nothing ran main(). So a NameError in the draw path -- one undefined variable, on the line that feeds a clip |
| `smoke_appstate.py` | Two ways a running system reported itself dead, both found on one box. | The Parking counter looked completely broken: the master page said the AI was not running, and the line counter read 0. The app was up, the chip was loaded and cars were being detected in the log the whole time. |
| `smoke_bands.py` | Threshold bands on a line chart. | A line chart says how a number MOVED. It did not say whether the number was acceptable -- for that an operator had to read a value off the axis and compare it, every time, against a limit that lived in their head or in a |
| `smoke_boardowner.py` | A board terminal our OWN workflow drives must not be blamed on Node-RED. | The detection apps are separate processes from the hub, and libgpiod reports every lgpio consumer as "lg" -- ours and everybody else's. So a pin one of our own rules was driving looked byte-for-byte identical to one the  |
| `smoke_boardrelease.py` | Handing a board terminal to another program (Node-RED, a customer script). | A GPIO line is ONE piece of hardware and every system on the box shares it, so this is a box-wide decision, not a per-system setting: two systems disagreeing about who drives DO0 is the conflict the page exists to preven |
| `smoke_branch.py` | The Branch family and the Trigger: every way out is a REAL port. | What this replaces: `logic.when` (Compare) and `output.alert` (Condition) published nothing of their own. They were resolved at compile time by walking BACKWARDS to the rule behind them -- the "transparent node" conventi |
| `smoke_cameradct.py` | A big MJPEG camera is decoded at the size the pipeline works at. | Found on 192.168.1.234: pcb_sensata at 190% CPU, and a per-thread profile (6 s, 200 Hz) put 60% of every sample on ONE camera's grab thread -- the 5MP USB camera (2592x1944 MJPG) -- split between cap.retrieve(), a full 5 |
| `smoke_camlimits.py` | Four things that were each somebody pressing a button and being surprised. | 1. A clip played as a timelapse. The header said 30fps because that is what the camera GRABS; the recorder is fed once per detection tick, which on a Hailo sharing its budget is 8. Four times fast. 2. Start on a Hub card |
| `smoke_camprobe.py` | Asking an IP camera what it can deliver actually asks it. | Reported as "I can't ask an RTSP camera for its resolution or its frame rate any more". The probe was running, reaching the camera and getting an answer -- and then throwing it away. |
| `smoke_camrecover.py` | A stalled live camera must be reopened, and a start() must never adopt a process it cannot prove is ours. | Both bugs presented as "the camera froze": an app that could never start kept spawning short-lived twins that touched the device, and the app that owned the camera then sat on its last good frame for ever while the overl |
| `smoke_camshare.py` | Two systems, one camera: the second consumes the first one's /raw feed instead of fighting for the device. That only works if the OWNER is in the registry -- and ownership was a flag set once, at the end of one open path | Seen on a box running two systems on one USB camera: the owner held /dev/video2, served /raw on its stream port perfectly, and the registry held no entry for it at all. The second system then retried the busy device for  |
| `smoke_camstall.py` | One frozen camera used to take a whole system's UI with it. | A stalled camera is INVISIBLE at the picture layer: every viewer path falls back to the last good frame, so the feed shows a perfectly still room, no `error` event reaches the browser, and StreamImg's stall watchdog has  |
| `smoke_camswitch.py` | Switching a camera off must actually stop it -- and keep it stopped. | Deactivating a camera released its device and then took it straight back, because two mechanisms disagreed about what "no frames" means: |
| `smoke_caps.py` | What an account may DO on a page it can open. | Before this, opening a page WAS the permission: every route that changed anything asked for master, so the two settings an account could have were "can change everything on this page" and "cannot see it". That is the wro |
| `smoke_cardcaption.py` | A card says what it was told to say, and nothing else. | Two settings that looked broken from the outside, for opposite reasons: |
| `smoke_cardedits.py` | A card's NAME and look survive a restart. | Reported as "I rename a card on Analytics or the Dashboard, and after a restart it is called the old thing again". Three separate holes, all with the same shape -- an edit written onto a row that is not the operator's to |
| `smoke_cardfont.py` | How big the words on a card are. | TWO settings, not one: a card's chrome (its title, and the note under its value) and the card's answer (the number or word it exists to show) are read at different distances. A wall display wants a huge number over a sma |
| `smoke_chipexclusive.py` | chip_holder() names who else is on an exclusive accelerator - as a diagnostic, not a gate on start(). | Found on a live Hailo-10H box running two detection systems: a Hailo-10H opens the chip EXCLUSIVELY (multi_process off there by design) and a second exclusive open does not fail, it TAKES the chip. The app that had it se |
| `smoke_clipload.py` | Recording a clip must not be visible in the live feed. | It was. Two costs sat squarely in the detection loop for as long as a clip was open: a full annotated render of a 920x640 frame per tick, and a SOFTWARE H.264 encode of it -- cv2's writer, called synchronously. Together  |
| `smoke_clipwindow.py` | A clip has to be WATCHABLE, and it has to include the run-up. | Two faults, both found on a live box: |
| `smoke_condition.py` | Condition says WHEN. Alert says what the alarm looks like. | They were one node, so choosing a threshold meant scrolling past a colour picker to reach it, and the node was called "Alert" -- naming the loudest thing it can cause rather than the thing it is. Split: |
| `smoke_cooldown.py` | An alert's cooldown, against a condition that FLICKERS. | The cooldown used to gate only the repeats: a rising edge fired whatever the clock said. That is fine for a condition that switches once and holds, and useless for every real one -- a zone count bouncing 1-2-1-2 with det |
| `smoke_counter.py` | ONE Counter, and the zone port decides where it counts. | The Object Counter counted the frame and the Zone Counter counted an area. Same modes, same timing settings, same OID output, same published metrics -- two palette entries, two inspectors, and an operator choosing betwee |
| `smoke_counttiming.py` | Counting timing: when does an object count, and does the canvas say so. | The rule is a pure function of (detections, clock), so it is asserted here rather than on a board -- which is the point of keeping it that way. |
| `smoke_crossrows.py` | One Data Table row per line crossing, with the direction on the row. | Reported from the live People Counting board (wf_5126): one person through the door wrote TWO rows at the same second, and the `status` column disagreed with in/out. Both are the mode working as written. The counter's live In/Out is a LEVEL |
| `smoke_csvnode.py` | The CSV node: a Query's rows, attached to a message. | A Query answers with a TABLE, and until now everything downstream took one number out of it -- a card, a variable -- because a message can only say a number. "How many rejects" is answerable; "which ones" was not, and th |
| `smoke_cycletime.py` | Cycle Time measures TIME, and an Alert may hand its result to a Formula. | Two changes, one idea: a node should answer the question it is named for, and nothing else. |
| `smoke_datasetchart.py` | A chart over a Data Table has to be ASKABLE, and its axis has to be readable. | Seen on a live box: a bar chart of a 0/1 column, summing that column and grouped by that same column, drawing exactly two bars labelled "0.0" and "1.0". Both halves of that are the product's fault: |
| `smoke_dbimport.py` | The export is a door that opens both ways. | Exporting one system's data to a SQLite file has worked for a while; there was no way back in, which makes an export a souvenir rather than a move -- and "we bought another box" is the case the system export was built fo |
| `smoke_dbpage_tools.py` | The Database page's three quiet gaps: a column could not be reordered, the whole-database export hid as an icon in the sidebar, and deleting a table meant knowing it lived inside the Edit dialog. |  |
| `smoke_dbports.py` | A Database node's ports must be VISIBLE, not merely present. | Every column on a Database node has always carried two handles -- one in on the left, one out on the right -- and they wired up correctly. None of them could be seen: the node's root set `overflow: hidden`, and a port si |
| `smoke_deleteclean.py` | Deleting a system deletes the system. | It stopped the app, stopped the master, disabled the entry, removed the package folder -- and left behind every document the system had written. A dashboard layout and a workflow store are keyed by slug and live OUTSIDE  |
| `smoke_deps.py` | What the product SHELLS OUT TO has to be installed by the installer. | None of it can come from pip: v4l2-ctl and nmcli are programs, the GStreamer elements are plugins, and OpenCV / PyGObject / hailo_platform are the distro's builds against the distro's libraries. So they were installed by |
| `smoke_detectorerror.py` | A detector that fell back said why, once, and then never again. | Reproduced on 192.168.1.234: `couting_test` (a new system) shares the box's ONE physical Hailo-8 with `pcb_sensata`, already running. Opening a second VDevice fails with HAILO_OUT_OF_PHYSICAL_DEVICES(74); run.py catches  |
| `smoke_dialogs.py` | The product asks its own questions. | window.confirm() renders the BROWSER's box: "192.168.1.234:5101 says" above the sentence, the host and port announced to an operator who is looking at a product, no styling, no destructive colour, no theme. It is also mo |
| `smoke_drafts.py` | A node nobody has wired anything into is a DRAFT, not a mistake. | Dragging a Snapshot onto the canvas and wiring it a minute later is how this page is used. Every one of those minutes painted the canvas with "compile errors" and a red node, over a node whose only fault was being new -- |
| `smoke_dupnodes.py` | A graph with duplicate node ids must still OPEN, and must stop being made. | One system reached four identical Alert nodes sharing one id and its Workflow page died outright -- "internal error - see Logs", a blank canvas, and the only tool that could have repaired the graph was the editor that wo |
| `smoke_facearrow.py` | The arrow on a faced line is the way IN, drawn as a movement. | It used to start ON the line and point off into the room: a label saying "this side is called in". An operator reading a doorway wants the other question answered -- which way must somebody walk to be counted -- so the a |
| `smoke_feedrearm.py` | A live feed that never paints, and the refresh that made it worse. | WebRTCVideo already has three watchdogs and falls back to MJPEG, but all of them can end in a session that is "connected" and carrying nothing -- and then Camera Setup's "Camera connecting -- opening the video stream..." |
| `smoke_footagecam.py` | Footage is browsed by CAMERA, and a delete finishes wherever you go. | The filter used to be "Zone alerts / Workflow captures" -- footage sorted by the MECHANISM that saved it, a distinction nobody looking for an incident has ever cared about. On a four-camera system the question is always  |
| `smoke_footageschedule.py` | A footage export that repeats on its own. | Emailing evidence was a manual act. Right for "send me this morning", wrong for a shift handover or a weekly record: those want to arrive without anybody remembering to make them, and a person who has to remember is a pe |
| `smoke_footagezip.py` | Download the ticked footage as one zip. | The page could already EMAIL a selection, and it exported the LIST as a CSV, which left the obvious thing missing: the files themselves, on the machine the operator is sitting at. A CSV of filenames is not evidence, and  |
| `smoke_gige.py` | Finding a GigE camera means asking the NETWORK, not asking Aravis. | The picker listed whatever `arv-tool` reported, which is only ever the cameras Aravis can already open. So the two failures an installer actually hits -- the Aravis plugin is not installed, and the camera is on a differe |
| `smoke_gigesize.py` | A GigE camera asked for a size it cannot reach does not give a smaller picture -- it gives NOTHING. | The caps never negotiate, the stream stops with "not-negotiated", and the app reports "camera disconnected: gave no frames after 3 reconnect attempts" of a camera that is plugged in, powered, answering discovery and read |
| `smoke_gigetune.py` | Exposure and gain: the two settings an industrial camera is useless without. | aravissrc leaves both on "off" -- meaning whatever the camera was last set to by whatever software touched it last -- so the same camera gave a black picture on one board and a blown-out one on the next, with nothing on  |
| `smoke_gridsize.py` | The grid a dashboard is drawn on: small, medium or big. | The GRID does not change with it -- twelve columns, always -- so a card that spans 3x2 still spans 3x2 and nothing on a page anybody already arranged moves. Only the pixels one row is worth change, so the whole page grow |
| `smoke_hailoasync.py` | Every camera's frame is in flight on the accelerator before any is waited for. | Measured on 192.168.1.234 (Hailo-8, HailoRT 4.23, yolov8m): one system alone on the chip ran 7.2 fps per camera exclusive and 7.1 through the service, so the service costs nothing -- and the zoo's number for this model o |
| `smoke_hailobatch.py` | A multi-context model is fed in batches, because that is what it rewards. | Measured on 192.168.1.234 (Hailo-8, HailoRT 4.23, PCIe Gen 2): yolov8m is a 3-context HEF, and through our InferModel path it ran 14.7 frames/s at batch 1, 23.0 at 4, 31.7 at 8 -- the part of its weights streamed from ho |
| `smoke_handlesync.py` | The wire that vanished. | React Flow measures every handle ONCE and caches it by id. A node whose ports change shape after that -- a Trigger growing a Zone port, a Math node gaining an input, a Branch gaining a condition, a Data Table gaining a c |
| `smoke_heatmap.py` | WHERE things happen, which no counter can answer. | A count says a zone was busy; a heatmap says which corner of it was, and that is the reading somebody rearranges a floor from. The map is published as one stat per cell (fn_<id>_c<row>_<col>) -- the same shape an object  |
| `smoke_idlejpeg.py` | Frames are encoded for VIEWERS, not for the loop's own satisfaction. | Two JPEG encodes ran on every frame of every camera, forever, whether or not a single person was watching: one in Camera.read() to keep the /raw cache warm, one in _draw() for the annotated stream. On the box this was fo |
| `smoke_inspectorui.py` | The node inspector's rhythm, made once instead of per node. | Every node's form is built from four things: a Field (label over control), a row of controls, a checkbox line, and a paragraph of explanation. Each had grown its own spacing one node at a time, so a form read as a stack  |
| `smoke_lineband.py` | A line's dead band is the operator's to set, and it is the same width whichever way the line is drawn. | Reported from the live door (wf_5126, Line 2): one person walking through yielded an IN and an OUT -- the tracked box shivers, and on that camera the shiver is wider than the fixed 0.012 band, so the anchor point cleared the far edge, held two frames, and came |
| `smoke_lineface.py` | Does this line have a FACE? | A line always knew a direction -- the positive side of the two points as they were drawn -- but nothing on screen ever said which side that was, and nothing let an operator say "this corridor has no in and out, I only wa |
| `smoke_linejitter.py` | A person standing ON the counting line is not crossing it, repeatedly. | Reported as "one object, and it counts four". Measured on the live door before the fix: track 76 crossed three times in NINE MILLISECONDS, and 15 of the 27 repeat crossings in a day came within 300ms of the one before. N |
| `smoke_lineoid.py` | The Line Counter knows WHICH object crossed, and can count each one once. | Two things, both asked for on a real door: |
| `smoke_linerows.py` | A line counter's rows are one PER CROSSING, and each says which way. | Reported as "the line counter doesn't count by in/out/crossings, it counts everything at once". It did -- in the event store, which is what every tally read back over a date range comes from. The loop collapsed a whole f |
| `smoke_loginlanding.py` | Signing in on a system port lands on the Systems hub -- always. | A master serves its own UI on its own port (5101, 5102, ...). Signing in there used to keep you there whenever the URL carried a deep link, and could keep you there even without one: the landing target came from a SECOND |
| `smoke_math.py` | The Math family: one node per operation, and any value may reach one. | The Formula was a single node with 34 operations and an input-count field. Its face could not say what it did until both were chosen, and finding "average" meant knowing to look for `avg` in a list that also held atan2.  |
| `smoke_modbus_server.py` | This box AS a Modbus device: a PLC polls US. | The other direction already existed -- the workflow's Modbus nodes read a PLC's registers. This is the mirror, and it is what lets a PLC ask "how many people are in zone 1" over the wire it already speaks, with no HTTP c |
| `smoke_nodecoverage.py` | Nine Workflow node types that had never been compiled by any test. | A full pass over `wf.REGISTRY` (94 node types) found analytics.proximity, analytics.guard, analytics.crowd, analytics.block, variable.api, variable.formula, output.mqtt_publish, output.webhook and output.webhook_publish |
| `smoke_modeldirs.py` | Built-in models and the operator's drop folder are DIFFERENT directories. | They used to be one: config.shared_models_dir() downloaded catalogue models into /mnt/nvme/model, which services/local_models.py scans and lists as custom -- so every fetched model appeared twice, once in Default Model w |
| `smoke_modelform.py` | The Add-a-model form offers what this board can actually run, and nothing whose only use is to get it wrong. | Two controls went. The ARCHITECTURE list held all six architectures the manifest format knows; on a board with a Hailo-8 in it, five of them were wrong, and a wrong one is not caught on save -- the model installs, sits i |
| `smoke_modelpicker.py` | The Detection Stream's Model picker on a system with ONE model. | The picker is drawn from the workflow editor's `models` list, which came from the model->labels map -- and that map named a single model only when a labels file sat beside it. A custom .hef with no labels list therefore  |
| `smoke_multiprocess_default.py` | [detector.hailo].multi_process defaults by architecture when nobody said. | This product's whole reason for `_busy_message` and `MAX_RUNNING_APPS` is that a Hailo-8 board routinely runs SEVERAL systems sharing one physical chip. Left to open the accelerator EXCLUSIVELY by default, the second sys |
| `smoke_nvmeprobe.py` | "Is there second storage" must ask about the place writes actually go. | Captures and recordings are refused without a second drive, because filling the media the OS boots from is how a recorder bricks a box. The test for it wrote a probe file at the MOUNT ROOT -- and a normally installed boa |
| `smoke_overlaycost.py` | The zone overlay is built once, not once per frame. | py-spy on a live 3-camera box put 26% of the whole detection loop inside _poly_grad: a linspace, a zeroed mask, a fillPoly, a full-size tint array and two float32 conversions of the region, rebuilt for every zone on ever |
| `smoke_pagetransition.py` | The handover from skeleton to page. | A skeleton and the page that replaces it are both `.page.active`, so the entrance animation played TWICE for one navigation: the placeholder rose and faded in, then a moment later the real content rose and faded in again |
| `smoke_panel.py` | Front panel: the framebuffer, the font, the page layouts, and the routes. | No hardware. Everything worth pinning here is a pure function of (page, facts, settings) -- which is exactly why the panel was built that way: the screens can be asserted on a laptop, and only the I2C write needs a board |
| `smoke_pinned.py` | The three numbers somebody is watching, kept where they can see them. | A graph with thirty rules publishes a hundred values, and Debug listed all of them in graph order under their RAW STAT KEYS -- live_s, net, avg_s -- while the node two inches away called the same numbers "Longest wait no |
| `smoke_portmetric.py` | Four ports, four cards, four different numbers. | Reported with a screenshot: a Line Counter's In / Out / Inside / Crossings ports each dragged into their own Card, and all four cards reading 4. They were all bound to `fn_<id>_in`. Dragging from a named output port IS t |
| `smoke_queryconsole_ux.py` | Four things an operator ran into in one sitting, all of them "the editor knows this and did not say it": | 1. The query console refused to work at all on a box whose database user lacks CREATEROLE, and told the operator to run a psql command that a dashboard cannot run. 2. A Query node could be written but not TRIED: the way  |
| `smoke_queryvar.py` | A Query stored in a Variable. | A Query could only ever draw a card. So a number that only the database knows -- "rejects this shift", "parts since the last changeover" -- could be LOOKED at and nothing else: no formula could use it, no When could watc |
| `smoke_responsive.py` | Every page except the workflow editor, on every size of screen -- and a PDF that is an A4 sheet whatever the window was doing. | Two separate jobs that are easy to confuse: |
| `smoke_runport.py` | A Math node's first input: where the calculation SITS, not another number. | `when` began as an opt-in gate -- tick "only when a condition holds", get a port, wire a condition, and the arithmetic computed while it held. Two things were wrong with that. The port existed only if somebody found the  |
| `smoke_savedqueries.py` | A SELECT worth keeping, kept. | The console was a scratchpad: type a statement, read the rows, close the tab. Anything useful written there had to be written AGAIN in a Query node, from memory, because the Database page and the canvas shared nothing -- |
| `smoke_schedule.py` | The clock as a rule. | Everything else on the canvas answers "something happened". A report that goes out at noon needs the other question -- "the time came round" -- and there was no node that could say it: the Email node's hours/days fields  |
| `smoke_sendwindow.py` | The send window belongs to the clock, not to each message. | An Email node and a Telegram node each carried their own "when it may send": a cooldown, a daily window, weekdays, a date range. Three problems with that, and the third is the one that cost a real report: |
| `smoke_setupwizard.py` | "What now?" -- asked on the one screen the guided setup never spoke on. | The tour was a Next/Skip bar in the top-right of the three pages it walks through, which meant it was only visible once you had already found the page it was going to send you to. A system created a minute ago lands on i |
| `smoke_setvartype.py` | What KIND of value a Set Variable stores. | The box beside "Set it to" only ever took a number, so "set the shift name to night" could not be drawn at all, and a count that must stay whole (12 people, never 12.4) had to be rounded by hand somewhere downstream. The |
| `smoke_sqlrange.py` | A hand-written Query can follow the date picker. | `%(from)s` / `%(to)s` existed for Telegram commands and for trying a statement by hand on the Database console. The DASHBOARD bound nothing: _sql_data called sqlconsole.run with no params, so a card whose statement named |
| `smoke_telegram.py` | A bot token belongs to the BOX, not to twelve nodes on a canvas. | The token and chat id used to be typed into each Telegram node, so a deployment whose twelve rules message one operations group held the same credential in twelve places -- on the canvas, in the saved graph, and in every |
| `smoke_textcolumn.py` | A text variable wired into a Data Table column actually lands in the table. | The bug this pins, found live on a customer graph (pcb_sensata on .234): a text constant compiles to no function -- the expression engine is numeric end to end -- so the wire from VIOLATION_STATUS into the `violation` co |
| `smoke_texttile.py` | A tile may say a WORD. | Everything a dashboard shows is a number, and for a good reason: the stats dict is read by the charts, the master sync and the Blynk pusher, all of which expect numbers. But "violate", "Zone 1" and the current shift are  |
| `smoke_tgcommand.py` | The box answering Telegram, not just shouting into it. | Every Telegram feature until now was outbound: something happened, a message went out. This is the other direction -- somebody types /summary from outside the network, taps a period, and gets the numbers back. |
| `smoke_tiers.py` | Four tiers, and what each one is allowed to be. | There used to be two: whoever holds the master account, and everybody else. So anyone who needed to add an operator had to be handed the master account, which hands them the whole box -- creating systems, the board's ter |
| `smoke_timebuckets.py` | Time, asked three ways, answered consistently. | A card over recorded data mixes three windows: which ROWS count (the period), how they are GROUPED (the bucket), and where the window's edges fall. Three defects lived in that seam: |
| `smoke_travel.py` | Travel Time: every trip number, and speed only when it can be honest. | The node had a "time / speed" mode that changed nothing but the ORDER of its metric list -- both numbers were published either way -- so it looked like a choice of measurement and answered nothing. And it published only  |
| `smoke_triggercolour.py` | A Trigger paints two things, and they are two settings. | It used to be ONE colour plus a "colour what" picker -- box, zone, or both. That could say "the zone is red" or "the object is red", never "the zone is amber and the object inside it is red", which is the drawing an oper |
| `smoke_updatefield.py` | -*- coding: utf-8 -*- What the first real OTA update on real hardware taught us (v2.1.1 -> v2.1.3 on the dev Pi). Four defects, each invisible on every bench and fatal in the field, each pinned here so it cannot come bac | 1. job.json recorded the config root as the relative "config"; the applier runs as its own systemd unit with no working directory, so it resolved it from / and every apply failed preflight on a box whose token was exactl |
| `smoke_webrtcrate.py` | The video runs at the camera's rate, whatever the accelerator is doing. | Reported with a screenshot: the status box read "camera fps 56.3" over a picture that visibly stuttered, with "hailo fps 17.4" beside it. Both numbers were true. The WebRTC publish was made from inside the detection loop |
| `smoke_widgetanim.py` | How a card ARRIVES, and how it moves when its numbers change. | Every chart in this product was built to hold still: each series said isAnimationActive={false}, because a wall display that redraws every few seconds must not twitch. That is still the default -- what changed is that an |
| `smoke_zoneedit.py` | While a zone is being dragged, the app takes its OWN outlines off the feed. | The app burns zone shapes into the video, so an unsaved drag showed two of them: the browser's shape under the pointer and the app's saved copy in the pixels underneath. The editor now asks the app to stop drawing them,  |
