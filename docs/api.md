# REST API reference

Living document - updated as the product changes.

Every table below was read off the `@router` decorators in `core/routes/`, not off a design note.
Where a gate is named it is the helper the handler actually calls. If this file and the code
disagree, the code is right and this file is the bug - the same rule
[`contract-v1.md`](contract-v1.md) states for the device contract.

The API is pure JSON over FastAPI. There are two servers and they mount different route sets:

```
hub (5100)        one card per system, platform administration, store, OTA
  └ master (5110, 5111, …)   one per system: its dashboard, cameras, models,
                             zones, workflow, data, integrations, devices
      └ agent (5101)         Contract v1 on a paired device
          └ detection app    MJPEG on its own stream_port (5102 by default)
```

Companion documents: [`features.md`](features.md) for what each capability is *for*,
[`contract-v1.md`](contract-v1.md) for the device side, [`conventions.md`](conventions.md) for the
rules the routes are written under.

---

## 1. The response contract

**Success** is a JSON object whose first key is `ok`:

```json
{"ok": true, "entries": [...], "summary": {...}}
```

**Failure** is the shortest true thing, with a real status code
([`conventions.md`](conventions.md) §8):

```json
{"ok": false, "error": "removing a system is an admin action", "rid": "9f3c1ab2"}
```

There is deliberately **no shared `ok()` / `err()` helper**. The success shape is written out at
each site; the failure shape is enforced centrally, on the error path, where getting it wrong
matters. A 200 carrying a failure is banned outright, because the hub's health logic keys off
status codes - a master that answered 200 for everything would look permanently healthy.

Three endpoints return `ok: false` at HTTP 200 on purpose, and they are worth knowing about
because they are not errors: `PUT /api/workflow` and `POST /api/workflow/compile` report
`"ok": not compile_errors`, since a graph that saved but did not compile is a *saved graph* -
auto-save must never lose canvas work - and `POST /api/functions/validate-expr` reports a rejected
expression the same way, because "this expression is invalid" is the answer the caller asked for,
not a failure to answer.

A handful of endpoints return no envelope at all: `GET /api/builder`,
`POST /api/builder/preview` and `GET /api/detector/webrtc` return bare payloads, and every binary
route (JPEG, MJPEG, ZIP, SDP, clip files) returns bytes with its own media type.

### Error handling

`install_error_handlers(app)` (`core/routes/logs.py`) is registered on both servers and turns
every exception into one recorded line and one parsable body:

| Situation | Status | Body |
|---|---|---|
| `HTTPException` raised by a handler | as raised | `{"ok": false, "error": <detail>, "rid": <id>}`; the exception's own headers are preserved; 5xx is also written to the log ring |
| The browser hung up mid-request (`ClientDisconnect`) | **499** | `{"ok": false, "error": "client disconnected", "rid": <id>}` - one INFO line, no traceback |
| Anything unhandled | **500** | `{"ok": false, "error": "internal error - see Logs", "kind": "<ExcClass>", "rid": <id>}` |

The 500 message stays server-side by design. What reaches the client is the exception *class* and
the request id; the traceback, the user and the client IP go to the log ring, which an
administrator reads on the Logs page. That is the whole reason the request id exists.

### Request ids

`core/middleware.py` assigns every request an eight-character id before anything else can want
one, and returns it as `X-Request-Id` on every response - including responses too boring to log.
The same id appears on the access line, on the recorded traceback, and in the `rid` field of every
error body, so "this screen showed an error" becomes one substring search on the Logs page instead
of a timestamp hunt.

The access log writes one line per request at INFO, WARNING for 4xx and ERROR for 5xx. Successful
requests under `QUIET_PREFIXES` - `/api/health`, `/static/`, `/assets/`, `/video_feed`,
`/system-static/` - are dropped, because the hub health-checks each master every few seconds and
a video feed is one request that never ends. A 4xx or 5xx on any of those is exactly the line
worth keeping, so only the successes are suppressed.

---

## 2. Authentication

### The session wall

Both servers install `auth_require(slug)` from `core/routes/auth.py` as an HTTP middleware. The
hub mounts it with `slug=None` - any valid account may see the hub - and each master mounts it
with its own slug, so a session for an account that was never granted this system is refused here
even though it works on the hub.

The session travels two ways at once:

- **Cookie** `cytron_session`: `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS, `path=/`, with a
  max-age of the configured idle window. This is what `<img>`, `<video>` and download requests
  ride, since they cannot set headers. Over HTTPS it is `__Secure-cytron_session`, and only that
  one is read there (a plain cookie an `http://` response planted is not a session on HTTPS); a
  sign-out over HTTPS ends both when they are the same account (`core/routes/authtls.py`).
- **Header** `X-Session`: per-tab identity, held in `sessionStorage` by the SPA so two accounts can
  be signed in in two tabs. A tab that presents `X-Session` **is** that user, whatever the
  browser-wide cookie says, and a header that fails to verify is a 401 - never a fall-through to
  the cookie. Falling back would silently turn a stale tab into the cookie's user, which is the
  exact bug the header exists to fix.

One deliberate interaction between them: a header-authenticated request whose *cookie* is dead
re-arms the cookie with its own token. A valid cookie, any user's, is never touched. Without this,
a dashboard left open past the idle window showed live numbers beside a dead video for ever -
every API call worked on the header and every stream 401'd on the cookie.

Every authenticated response carries `Cache-Control: no-store`, so session-only content does not
survive a logout in the browser's back/forward cache.

### `OPEN_PREFIXES`

Paths that never need a session:

```
/login   /static   /assets   /system-static   /api/public
/api/auth/login   /api/auth/logout   /api/auth/forgot   /api/auth/reset
/favicon   /forgot   /reset-password
```

### `OPEN_EXACT`

Paths open as themselves only, never as a prefix (`auth.is_open`):

```
/api/health   /api/health/box
```

`/api/health` is open on purpose: the hub probes each master with it, and "device down" must stay
separable from "token wrong". `/api/health/box` is the Wi-Fi switch page's probe (section 3.6c).
Both used to be open because `/api/health` was a prefix; now each is named, and any future
`/api/health/<x>` is behind the session wall unless it is added to `OPEN_EXACT` on purpose. The
recovery paths are open by nature - their audience has no session.

Matching is **boundary-anchored** (`auth._under`): a path matches a prefix only if it equals it or
continues with `/` or `?`. The earlier bare `startswith` would have silently unauthenticated any
future route that merely began with an open prefix - `/api/publicity`, `/api/healthz-admin`,
`/loginsessions`.

`PROTECTED_PREFIXES` is the mirror image: `/video_feed` and `/footage` are real endpoints living
outside `/api/`, and they are listed so they get the same 401 an `/api/` path gets. Before that
list existed they ran with no session at all, and anyone routable to the board could watch a
paired camera and delete its recordings.

### CSRF

Every POST, PUT, PATCH and DELETE is checked before anything looks at the session. If `Origin`
(falling back to `Referer`) names a different **hostname** than the request, the answer is
`403 {"ok": false, "error": "cross-site request refused"}`.

Three details are deliberate:

- The **port is ignored.** The hub on 5100 and every master on 511x are one product to the
  operator, and the SPA links across them by design.
- **Open endpoints are checked too.** Forced logout and flooding `/api/auth/forgot` are both
  cross-site tricks against endpoints that need no session.
- **A request with neither header is allowed.** That is a CLI or `curl` caller, which has no
  ambient cookie and is therefore not a CSRF vector; the fleet's own tooling calls these
  endpoints.
- **Over TLS the page must be `https` too**. HTTP stays open on the same
  host, so an `http://` page there can be rewritten by anyone on the LAN; a write over HTTPS (or
  through the hub's relay with `X-Forwarded-Proto: https`) whose `Origin`/`Referer` is `http://`
  is refused even when the host matches. Plain HTTP is unchanged.

There is no CSRF token. The `SameSite=Lax` cookie already blocks cookie-bearing cross-site POSTs
in a current browser, but "the browser will handle it" is not a server-side control - the
Origin check is, and it costs nothing and needs no token round-trip through every form in the SPA.

### 401 semantics

A **session-level** 401 carries `X-Session-Required: 1`:

```
401  {"ok": false, "error": "login required"}              X-Session-Required: 1
401  {"ok": false, "error": "not allowed for this system"} X-Session-Required: 1
```

The SPA bounces to `/login` on that header and only on that header. **Endpoint-level** 401s - bad
pairing credentials, a failed re-authentication - deliberately lack it and are surfaced to the
user as a message, because bouncing someone to the login screen for typing a device token wrong
would be a lie about what went wrong.

An unauthenticated request to a path that is neither `/api/` nor a protected prefix is **not**
401'd: it is served the SPA shell so React can render its own login view. There is no server-side
redirect; the SPA owns `/login`.

### The gates

Inside a handler, four helpers do the rest. They are per-module by design - `conventions.md` §4
keeps a page's files to itself - but they are all thin wrappers over `core/services/auth.py`.

| Gate | Passes for | Refuses with |
|---|---|---|
| `_require_admin` | the administrator account and any developer (`auth_service.is_admin`) | 403, wording varies by module |
| `_require_operator(request, slug, action)` | an admin, or any account granted that slug (`auth_service.allowed`) | 403 `"<action> needs access to this system"` |
| `_require_developer` | an account with `developer = true` in `users.local.toml` | 403 `"<action> is a developer action"` |
| `_require_session` | any authenticated caller - a belt-and-braces re-check in `proxy.py` | 401 |

In the tables below, **session** means *no gate beyond the middleware*: a valid session is still
required. Role names grant nothing by themselves; what a caller may reach is decided by the
gates above and the account's systems allowlist.

---

## 3. Hub API

Mounted by `hub_server.py`. Every router is a bare `APIRouter()` - no prefixes are added at
include time, so the paths below are exactly what the decorators say.

### 3.1 `core/routes/hub.py` - systems, the grid, platform data

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/bootstrap` | session | Shell context for the hub SPA: brand, user flags, `mode: "hub"`, empty system/nav |
| GET | `/api/hub` | session | The systems grid - one card per *visible* system, with probe results, groups, `system_count`, `max_systems`, platform version, `storage_alarm` (the NVMe-not-mounted banner: `{active, text}`) |
| GET | `/api/hub/templates` | session (content filtered by developer flag) | What the create panel may start a system from; non-developers see built-in formats only |
| GET | `/api/hub/formats` | session (`editable` from developer flag) | The built-in formats in full, plus zone modes, categories, workbench map |
| POST | `/api/hub/systems` | admin | Create a system from a name and a format template; scaffolds, grants the creator, spawns the master. Optional `port` (custom) is checked by the same rules as `/api/hub/ports/check`; left out = next free |
| PATCH | `/api/hub/systems/{slug}` | admin | Edit system: `{name, port}`. The name is display only - the slug is identity. `port` moves the master: stopped on the old port, started on the new one, put back on the old one (400, nothing saved) if it does not answer within 45 s; answers `{port, old_port, moved, restarted, url}`. Refused in words for a reserved or taken port, a systemd-run master (irivai-master@<slug>) or one on another host. No `port`, or the same port: the old rename |
| GET | `/api/hub/systems/{slug}/port` | admin | (`core/routes/sysport.py`) Edit system's port facts: `{port, stream_port, suggested, editable, reason}`; the video stream port never moves |
| GET | `/api/hub/ports/check?port=&slug=` | admin | (`core/routes/sysport.py`) `{suggested, port, usable, detail}`: the next free master port, and whether a typed one can be had (hub port and its pending move, box services, other systems' master/stream/Modbus ports, a bind test); `slug` = the system being edited |
| DELETE | `/api/hub/systems/{slug}` | admin | Stop the master, disable the system, delete the folder only if it was tool-generated |
| GET | `/api/hub/systems/{slug}/export` | admin | Download a whole system as one JSON file; no ports, no credentials |
| POST | `/api/hub/systems/import` | admin | Create a system from an uploaded export (multipart `file` + `name`) |
| POST | `/api/hub/systems/{slug}/detection` | operator | Start or stop the detection app (`{on: bool}`) |
| POST | `/api/hub/systems/{slug}/power` | operator | Start / stop / restart a **loopback** master; a remote-host master is refused with 400 |
| POST | `/api/hub/systems/{slug}/autostart` | operator | Flip the autostart flag, and bring the system up now when switching it on |
| POST | `/api/hub/systems/{slug}/capture` | operator | The snapshot / record switches for one system |
| POST | `/api/hub/systems/{slug}/image` | admin | Upload the card's cover image |
| GET | `/api/hub/systems/{slug}/image` | session | Serve the cover image (private, 1-day cache) |
| DELETE | `/api/hub/systems/{slug}/image` | admin | Remove the cover image |
| GET | `/api/hub/streams` | session (scoped to visible systems) | Live-wall data: each system's stream-capable devices and local cameras |
| GET | `/api/hub/pipelines` | session (scoped) | Each system's pipeline: camera, model, backend, fps, running, pid, uptime, autostart, caps |
| GET | `/api/hub/health` | session (scoped) | Host and NPU health, plus each visible system's device health from its master |
| GET | `/api/hub/retention` | session | The retention policy, what this box allows (`limits`: device data up to 60 days, or 365 with the data on the NVMe; 12 months for the database), `policy` (what applies now), `saved` (the periods as saved; a saved 365 is kept while the NVMe is not mounted and applies again when it is back), `notice` (said once per spell while a saved period is above `limits`), `alarm` (the NVMe-not-mounted banner), and the sweep schedule |
| POST | `/api/hub/retention` | admin | Update the policy and sweep every system now; a period above `limits` is refused with a plain sentence naming the row and its maximum |
| POST | `/api/hub/retention/nvme/forget` | admin | "This box no longer uses the NVMe drive": drops storage_watch's seen-on-this-drive memory and re-checks; `{forgotten[], alarm, still_expected: {mount: why}}` - the alarm stays, and `why` says so, while `/etc/fstab` or a configured path still names the drive (`core/routes/storage.py`) |
| GET | `/api/hub/storage/new` | master | "New storage detected" popup poll: `{prompt: drive|null, job, can_setup, needs, systems}`; `prompt` is the first drive the box may set up that was not declined, only while nothing is mounted at `/mnt/nvme` (`core/services/storage_setup.py`) |
| GET | `/api/hub/storage/devices` | master | "Set up storage" on Data Retention: every drive the box may set up, `declined` flagged, plus `job`, `can_setup`, `has_drive` |
| GET | `/api/hub/storage/inspect?id=` | master | Before "use it as it is": `{foreign, message}`, whether the drive already holds another box's IRIV software (`cytron/store`), from a read-only look by the root helper; the box's own drive (active fstab line) is never looked at |
| POST | `/api/hub/storage/decline` | master | `{id}`: the admin's No, remembered for that drive (WWN / serial / UUID); audited |
| POST | `/api/hub/storage/setup` | master | `{id, format, confirm}`: format (`confirm` must be `FORMAT`) or mount what is on it, at `/mnt/nvme` with an fstab line by UUID (nofail), through the root helper `/usr/local/lib/irivai/storage-setup.py` in the background; progress in `job`; refused when the id is not in the box's own scan; audited, outcome to the Inbox |
| POST | `/api/hub/systems/{slug}/data/delete` | admin | Purge one system's recorded data; `scope` ∈ `all` / `events` / `footage` / `counters` |
| GET | `/api/hub/systems/{slug}/data/reset-status` | session | Progress of a counters reset - readable by any signed-in user on purpose |
| POST | `/api/hub/groups` | developer | Create, rename, reorder and delete the grid's folders in one write |
| POST | `/api/hub/systems/{slug}/group` | developer | File a system under a folder - and the folder is the publish switch for built-in formats |

### 3.2 `core/routes/store.py` - the online template store

Developer-only, **browsing included**. Hiding a page hides the button, not the endpoint, and a
customer appliance's administrator is not meant to be shopping for product content.

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/hub/store/catalog` | developer | The catalogue; `force=true` bypasses the cache |
| POST | `/api/hub/store/install` | developer | Start an install from a license or template `key`; returns the initial job status |
| GET | `/api/hub/store/install/{key}` | developer | Poll install progress; on `done`, folds the new system into the live registry |
| GET | `/api/hub/store/license` | developer | Current license state for this box |
| POST | `/api/hub/store/license` | developer | Activate a license key |
| DELETE | `/api/hub/store/license` | developer | Clear the stored key |

`StoreError` maps to 400 - a bad key, an unverifiable bundle and an unreachable catalogue are all
things the caller can act on.

### 3.3 `core/routes/update.py` - platform OTA

Admin-only, deliberately **not** developer-only: a customer appliance has no developer account,
and an appliance that could not update itself would be an appliance nobody could fix.

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/hub/update` | admin | Current update state for `?channel=` (`stable` default; `dev` needs a developer account on a dev box) plus `ota_ready`, `dev_available` (this account may use the dev channel here) and `dev_box` (this box is a dev box, whoever asks). Never returns the token, the ETag or the asset URL. Answered while account setup is still pending (a read; every POST below answers 409 until setup is done) |
| POST | `/api/hub/update/check` | admin | Force a release check now. Runs off the event loop; while one check is out a second call answers the cached state with `checking: true` at once (one GitHub request, never two) |
| POST | `/api/hub/update/token` | admin | Store `{token, channel}`: the customer token (`stable`, key `token`) or, developer account on a dev box only, the developer token (`dev`, key `dev_token`, read against the separate dev release repo). Each channel's token is sent only to its own repo. Answers only `{configured: bool}` |
| POST | `/api/hub/update/apply` | admin | Apply an update to `{version}` |
| POST | `/api/hub/update/rollback` | admin | Put the previous release back |
| GET | `/api/hub/update/status` | admin | Progress of the in-flight job. Answered while account setup is still pending |
| POST | `/api/hub/update/auto` | admin | `{enabled}`: "Check for updates automatically" (`core/routes/update_local.py`). Off by default on every box; off, the background checker never contacts GitHub (only Check now and Update do). On, it checks as soon as the box has internet (default route in /proc, then one TCP probe to api.github.com:443 while offline) and then every `[check] interval_hours`. One switch for both channels, stored as `auto_check` in `config/update.local.toml` (carried by every update). Answers `{auto_check}`; GET `/api/hub/update` carries `auto_check` too |
| POST | `/api/hub/update/manual` | admin (developer too on a dev box) | "Update manually": the body IS the offline update file `irivai-update-v<version>.tar.gz` (raw, `application/octet-stream`, no multipart; the filename is never read), streamed to `<update dir>/offline/` (0700) and capped at 512 MB, refused up front when `Content-Length` is over the cap or the store lacks room. Before anything is accepted: exactly the three regular members `bundle.tar.gz`, `manifest.json`, `manifest.sig` (no links, devices, `../` or extra entries), the manifest's ed25519 signature with this box's OTA key, its `app` (a dev file only on a dev box, a customer file only on a customer box), the bundle's sha256 and size. Older than the running version is refused. Answers `{offline: {id, version, notes, same_version, channel, size}}`; one file waits at a time, for an hour (gone on cancel, on expiry, after a reboot, at hub start). A refusal is SYS-1031 with the plain reason in `details` |
| POST | `/api/hub/update/manual/install` | as above | `{id, reinstall}`: install the waiting file through `scripts/apply_update.py` as a local-source job (no token, no download; preflight, signature and hash again, flip, health gate, rollback unchanged). The same version needs `reinstall: true` and is built as `v<version>-r<n>` beside the live release. The applier removes the file when the job ends. 409 SYS-1032 while a job runs |
| DELETE | `/api/hub/update/manual` | as above | Cancel: remove the waiting file |

`UpdateError` maps to **409** when a job is already running and 400 otherwise - a second apply is
a conflict, not a bad request.

### 3.4 `core/routes/audit.py` - mounted on the hub *and* every master

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/audit` | admin | One page of the audit log: `action`, `username`, `limit` (1–1000), `offset` |

It returns `has_more` rather than a total, and hides developer accounts' rows from non-developer
callers.

### 3.5 `core/routes/logs.py` - mounted on the hub *and* every master

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/logs` | admin | Query the in-memory ring: `level`, `logger`, `contains`, `since`, `limit` (1–1000) |
| POST | `/api/logs/clear` | admin | Empty the ring, recording who did it |
| POST | `/api/logs/purge` | admin | Clear Log: empty every store the Logs page reads (hub ring, every master asked as the operator, each app spool; app.log is kept and its lines hidden above a clear mark, `logs_cleared.json`), then write a trace record "Log cleared by <user>, N records removed" in EVERY process that cleared (no way to skip it) and a `logs.clear` audit entry (user, systems, count). The audit trail itself is not cleared |

Admin-only because log lines carry paths, hostnames and stack traces. The ring is **per process**:
this hub's ring holds hub failures, and each master's holds its own. That is why Logs is a page on
every process rather than one "all logs" page on the hub - the hub refuses to proxy, so a single
page would quietly show a fraction of the answer.

### 3.6 `core/routes/capacity.py` - mounted on the hub *and* every master

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/capacity` | session | What the box has left: `systems`, `running` apps, distinct `cameras`, and the caps |

Mounted on both so the header meters do not vanish when an operator opens a system. For a
developer, `max_systems` and `max_running` report 0 - the workshop is not a deployment - while
`max_cameras` stays the chip's real cap, which binds everyone.

### 3.6b `core/routes/hubport.py`, `hostname.py`, `boxrestart.py` - the software port and the box name (hub only)

Neither changes on Save. Both are saved for the next restart of the box and applied by the
hub's first start in a new boot (`hub_server.main` -> `core/services/boxpending.py`
`apply_at_start`), before the port is resolved and before anything advertises the name. Until
then the box answers on the same port and the same name. A refused change at the restart keeps
the old value, is shown on the card for that boot (`restart.last`) and posts an admins' Inbox
notice. A hub restart in the same boot (a crash, an OTA update) applies nothing.

Every GET and save answers a `restart` block shared by both cards:
`{port, hostname, any, can_reboot, applies, current: {port, name}, after: {port, name, name_url, ip_urls[]}, last}`
(`applies`: whether THIS hub is `irivai-hub.service`, the one unit that applies a saved change;
false from any other hub, whose saves are refused with 400 "not the box's own service" and whose
cards disable Apply and promise nothing)
(`last`: `{boot, at, port_ok, port_from, port_to, port_detail, name_ok, name_from, name_to, name_detail}`
for the restart that started this boot, or null).

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/hub/port` | master or developer | `{port, running, pending, default, source, systems[], url, restart}` - the configured port, the one this process bound, the one saved for the restart |
| POST | `/api/hub/port/test` | master or developer | `{port}` -> `{port, usable, current, detail}`: range, the box's own services (agent 5101, MediaMTX, MQTT, Postgres, SSH, DNS, HTTP, mDNS, Node-RED, Modbus 5020), every system's master, stream and Modbus port, and a live bind test |
| POST | `/api/hub/port` | master or developer | `{port}` -> saves `[hub].pending_port` (and the boot) in `config/systems.local.toml`; `[hub].port` and `[hub].url` do not move. The running port cancels a pending change. 400 with the reason when refused. Answers the status |
| GET | `/api/hub/hostname` | master or developer | `{hostname, local_name, default, is_default, shared_name, can_set, helper_installed, via_name, port, pending, restart}` |
| POST | `/api/hub/hostname` | master or developer | `{name}` -> saves `[box_name].pending` (and the boot) in `config/network.local.toml`; the box keeps its name. The current name cancels a pending change. 400 for a name that is not an RFC 1123 label, for a name another device on the LAN already answers (`avahi-resolve`, when installed), and for a box without the root helper ("needs a one-time update by the installer"). Answers the status |
| POST | `/api/hub/restart` | master or developer | reboots the box (the `reboot now` sudoers rule, through the front panel when it runs) so both apply; the reply `{restarting, after}` leaves first. 400 when nothing is waiting, 409 when the box has no reboot rule. The page hands its session (`#session=`) only to an address in `after.ip_urls` (the browser's own first, then the box's interfaces), never to `after.name_url` |

The port needs no root: in `irivai-hub.service` (read from the process's cgroup) the setting
wins over a `--port` baked into a pre-October unit (adopted into the file only while it has
none), and only that unit applies the pending changes; anywhere else, including a second hub
under its own unit sharing the config dir, an explicit `--port` wins. The name is set at the restart by `/usr/local/lib/irivai/set-hostname.py`
(root-owned, name on stdin, re-validated there; `sudo -n /usr/bin/python3 -I <helper>`). The
pending port counts as taken for new systems (`provision._ports_in_use`) and the MQTT broker
form (`hubport.claimed_ports`).

### 3.6b2 `core/routes/https.py` - HTTPS with the box's own root certificate (hub only)

Unlike the port and the name, applied at once: HTTPS is an extra listener in the hub process
(`core/tlsserve.py`), so switching it on, moving it or off moves no HTTP address. Every save is
audited (`https.save`, `https.new_leaf`, `https.new_root`, with `result` ok/fail). Off on every box
until switched on; nothing is generated before. Design: `docs/architecture.md` sec 6.1.

The status block (every answer): `{enabled, port, default_port, redirect_http, listening, port443,
port443_detail, helper, error, root: {fingerprint, subject, not_after, created} | null, leaf: {names[],
ips[], not_after, issued, reason} | null, uncovered[], page_https}`. Public facts only; no key, ever.

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/hub/https` | master or developer | the status above. `page_https`: this request came over TLS |
| POST | `/api/hub/https` | master or developer, audited | any of `{enabled, port, redirect_http}` (booleans must be `true` to mean on). `enabled: true` makes the root and the certificate first if there are none. `port` is checked like the Software port (1024-65535, the box's services, every system's ports, a bind test; 8443 is reserved for this). 400 with the reason when refused. The listener and the 443 redirect follow before the answer |
| POST | `/api/hub/https/regenerate` | master or developer, audited | `{"what": "leaf"}` a new server certificate (fresh key; clients notice nothing), or `{"what": "root", "confirm": true}` a new ROOT, which every client must trust again (400 without `confirm: true`). 429 inside the cooldown (15 s leaf, 60 s root). 400 when the box's clock is not set |
| GET | `/api/hub/https/root.crt` | master or developer | the PUBLIC root certificate as an attachment, `application/x-x509-ca-cert`: PEM `iriv-edge-ai-root.crt`, or DER `iriv-edge-ai-root.cer` with `?format=der` (any other value is the PEM). Built from the parsed certificate, never from a path. 404 before HTTPS was ever switched on, 409 when the stored root is damaged |

### 3.6c `core/routes/network.py` - the box's network (hub only)

Site admin and above (`network._require_admin` -> `auth.is_site_admin`): how the box reaches
the network is part of running the operation on it. Only the access-point save is listed here
so far, plus the one open route in this file.

| Method | Path | Gate | Purpose |
|---|---|---|---|
| POST | `/api/hub/network/ap` | site admin | Switch wlan0's job LIVE, no reboot (`core/services/wifiswitch.py`): `{on: true}` = access point on the stored name and key; `{on: false}` = Wi-Fi, with `{network}` (a saved profile), `{ssid, password, hidden}` (a new network, through the wifi-profile helper) or neither (any saved network in range). Answers at once with the `GET /api/hub/network` status, `switch.state` `switching`; poll it for `done` / `failed` with `message`, `reason` (`wrong_password`, `not_found`, `no_address`, `timeout`, `failed`) and `address`. A join that has not connected with a real address within 35 s is reported `failed` with `hotspot_off: true` and the message ending "The hotspot is off. Turn the hotspot on to reach this device without Wi-Fi."; the access point is NEVER raised by itself (2026-10-14). An AP that will not start puts the previous Wi-Fi back. `[ap].mode` becomes `wifi` as the hotspot is taken down for a join (whatever the join does) and `ap` once the hotspot broadcasts. A caller on wlan0 gets the reply before the radio moves (3 s). 400 in words for an unknown saved network, no network to join, or a switch already under way; other network writes answer 400 while one runs |
| GET | `/api/health/box` | **open** (`OPEN_EXACT`), **cross-origin readable** | `{ok, box, switch}`: `box` is `hostinfo.box_id()` (16 hex characters of a hash of the serial), `switch` one word of `wifiswitch` state (`idle`, `switching`, `done`, `failed`). Nothing else: no address, name, version or network. The **only response on the hub readable from another origin** (`Access-Control-Allow-Origin: *`, set on this response alone; `Cache-Control: no-store`; a simple GET, no credentials, no preflight). The Network page, still running at the address the box left, probes every address in `switch.candidates` with it after a Wi-Fi / AP switch and opens the page at the first one answering with this box's `box_id` and a finished switch (`WifiSwitchOverlay.jsx`) |
| POST | `/api/hub/network/ap/save` | site admin | `{ssid, password, address}` (any may be left out: kept as stored) -> saves `[ap].ssid` / `[ap].password` / `[ap].address` (the hotspot's own IPv4 address, default 192.168.1.1; a private host address, refused otherwise) in `config/network.local.toml` (0600) through `core/services/apconf.py`. While the AP broadcasts the change is applied at once (the one AP profile restarted through the existing wifi-profile helper, key on stdin, or the legacy hotspot command); with AP mode off it is only stored. Answers the `GET /api/hub/network` status plus `ap_save: {applied, message}`, `applied` one of `unchanged`, `stored`, `now`, `scheduled` (the request came in over the AP itself: restarted a few seconds after the reply) or `failed` (saved, not restarted; used after a reboot). 400 with the reason in words for an invalid name (1-32 bytes, no control characters) or password (8-63 printable ASCII; the default `iriv1234` is accepted like any other). The key is never written to a log line |

### 3.7 Inline on the hub app

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/health` | **open** | `{ok, role: "hub", version, systems, masters_configured}` |
| GET | `/api/health/box` | **open**, cross-origin readable | Mounted from `core/routes/network.py` (section 3.6c): which box answers here, and its Wi-Fi switch state |
| GET | `/{full_path:path}` | session middleware falls through for non-`/api` paths | The SPA shell (`no-store`); `api/…` → 404; no build → 503 |

`version` is the constant frozen at process start (`core/version.py`), not a file read. The OTA
health gate uses it to prove the **new** process is serving - a live file read would answer with
whatever the updater just wrote to disk while the old process was still up.

`hub_server.py` also calls `deploy.mount_dev(app, config_dir)`, which mounts the owner's authoring
and publishing routes from `core/dev/` - but only on a box whose `config/deploy.toml` says
`mode = "dev"`, and a customer install has no `core/dev/` on disk to mount at all. Absent code
cannot be called; the per-route developer check is the inner lock for boxes that do carry it.

---

## 4. Master API

Mounted by `master_server.py`, one process per system. Again, every router is bare - no include
prefixes. `audit`, `logs` and `capacity` are shared with the hub and are documented in §3.

### 4.1 `core/routes/auth.py` - accounts and sessions

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/public` | **open** | Pre-login context: brand, whether a recovery address exists, `hub_url` |
| POST | `/api/auth/login` | **open**, rate-limited by IP and username | Sign in; sets the cookie and returns the per-tab token |
| POST | `/api/auth/logout` | **open** | Revoke this session; clear the cookie if it is the same account |
| POST | `/api/auth/forgot` | **open**, rate-limited by IP | Mail a reset link to the recovery address |
| GET | `/api/auth/reset` | **open**, rate-limited | Is this reset token still valid? |
| POST | `/api/auth/reset` | **open**, rate-limited | Set a new password from a token |
| GET | `/api/auth/me` | session | Who am I, plus `is_admin` / `is_developer` |
| POST | `/api/auth/password` | session + current password | Change my own password; revokes my other sessions |
| POST | `/api/auth/username` | session + must be the admin account + current password | Rename the administrator account |
| POST | `/api/auth/keepalive` | session | Push this session's idle expiry out |
| GET | `/api/auth/sessions` | session (admins see all, others their own; developer rows only to a developer) | Live sessions, plus `devices`: registered sign-in devices with their sessions, status, IP, best-effort MAC, and the per-account cap (`device_limit`, `device_count`, `device_capped`) |
| DELETE | `/api/auth/sessions/{sid}` | session, owner or admin | End one session (the device stays registered) |
| POST | `/api/auth/sessions/revoke-others` | session | End all my other sessions (devices stay registered) |
| DELETE | `/api/auth/devices/{ref}` | session, owner or admin (never a developer's device for a customer) | Remove a registered device: unregister it and end all its sessions; it must sign in and register again |
| POST | `/api/auth/devices/remove-others` | session | Remove every other device of my account |
| GET | `/api/auth/account-setup` | admin | First-run setup context |
| POST | `/api/auth/account-setup` | admin (+ current password to rename or change it) | Recovery email, admin name, admin password |
| GET | `/api/users` | admin | Accounts with their systems, roles and grants |
| POST | `/api/users` | admin | Create an account |
| PATCH | `/api/users/{username}` | admin | Edit systems allowlist, role, enabled, per-system categories |
| POST | `/api/users/{username}/password` | admin | Reset a user's password; revokes all their sessions |
| DELETE | `/api/users/{username}` | admin | Delete an account |
| POST | `/api/roles` | admin | Add a role label |
| DELETE | `/api/roles/{name}` | admin | Remove a role label |

The account-management routes additionally refuse the built-in administrator row and every
developer account (`_refuse_builtin`, `auth._refuse_if_developer`), so the account that can
always recover the box cannot be locked out or deleted from the page.

### 4.2 `core/routes/spa.py` - the shell and its page payloads

Read-only by policy: nothing here writes.

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/bootstrap` | session | Shell context: brand, user, system, capability-driven nav filtered by the caller's category grants, sidebar, `hub_url` |
| GET | `/api/main` | session | Dashboard tiles, devices against monitoring thresholds, live stats, alerts |
| GET | `/api/platform` | session | Platform-wide stats across devices and events |
| GET | `/api/cameras` | session | Camera rows derived from this master's devices |
| GET | `/api/stream` | session | Devices declaring the `stream` capability |
| GET | `/api/footage` | session | Devices declaring the `footage` capability |
| GET | `/api/settings` | session (returns `is_admin`) | Device configs, master config, system slugs |
| GET | `/api/analytics` | session (returns `editable`) | Widget pages for a validated `range`, the workflow widget catalog, day-count dots |

### 4.3 Dashboards, analytics and functions

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/dashboard` | session | The whole Dashboard payload: run state, computed widgets, setup steps. Control cards carry whether the viewer may use them (`can_press` on an Inject button, `can_edit` on AI model classes and Class filter) |
| GET | `/api/builder` | session | Saved layout, bindable keys, palette vocabulary |
| PUT | `/api/builder` | admin | Save a layout (`partial: true` merges) |
| POST | `/api/builder/preview` | session | Render an unsaved layout against live data |
| POST | `/api/builder/publish` | admin | Package the layout as a system zip, or install it |
| GET | `/api/builder/export` | session | Download the raw layout JSON |
| GET | `/api/overview/live` | session | Main-page poll: run state, zones, verdicts, live values |
| GET | `/api/overview` | session | Platform Overview stats |
| GET | `/api/functions` | session | Function documents, presets, variables, live values |
| PUT | `/api/functions` | admin | Replace the function list |
| POST | `/api/functions/validate-expr` | session | Validate an expression and report the variables it reads |
| GET | `/api/history` | session | Aligned stat time series for `keys` over `range` |
| GET | `/api/host/meter` | session | Cheap CPU / NPU / fps sample for the hardware footer |

### 4.4 Workflow

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/workflow` | session | The canvas, the node registry, variables, compile errors |
| PUT | `/api/workflow` | admin + `_require_python_rights` | Save the canvas; apply the compiled artifacts if it compiles clean |
| POST | `/api/workflow/compile` | admin | Dry-run compile, writing nothing |
| POST | `/api/workflow/modbus-scan` | `workflow.edit` | `{server, action: "start"\|"stop", first?, last?}`: scan a Modbus Server for the Unit-Ids that answer (one FC3 read of register 0 per id, read-only). Runs in the system's app, which is started (AI off) if it is not up. Answers `{ok, seq}` |
| GET | `/api/workflow/modbus-scan` | `workflow.edit` | The latest scan: `{scan: {seq, server, state: "scanning"\|"done"\|"stopped"\|"error", first, last, at, found: [unit ids], error}, app_running}` |
| POST | `/api/workflow/class-filter` | `workflow.edit` | The dashboard's Class filter card: `{node, running}` runs or pauses one NAMED Class Filter (`core/routes/class_filter.py`) |

**Class Filter names.** With more than one Class Filter on the canvas, each needs its own name
(case and outer spaces ignored): `PUT /api/workflow` stores the canvas but does not apply it, and
answers one error per unnamed or repeated filter plus a general line ("Name each Class Filter: this
workflow has 2, and the dashboard lists them by name"). One filter alone may stay unnamed. The
compiler itself never refuses: graphs saved before the rule, templates, the Zones page's mirror and
the first-visit migration are named from their classes on load (`core/services/class_filter.py`).
A paused filter is `[class_filters] paused` in the system's `config.local.toml`; the running app
re-reads it on its config poll and holds back the rules and outputs after it (no restart).

`_require_python_rights` is a second gate over the first: changing a Python node's code needs both
the box opt-in (`config/workflow.toml` `[python].enabled`) and the developer flag.

### 4.5 Cameras, models and detector control (`core/routes/models.py`)

This is the largest router in the product - the Camera Setup and AI Models pages are one control
surface over the local detection app. The pattern is consistent: **GET is session-gated, POST is
admin-gated.**

Models and manifests:

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/models` | session | Manifest listing (+ `editable`) |
| POST | `/api/models` | admin | Create or update a manifest |
| POST | `/api/models/upload` | admin | Upload weights plus manifest in one multipart request |
| GET | `/api/models/presets/{name}` | session | A manifest draft for a Model-Zoo preset |
| GET | `/api/models/{name}/file` | session (`?what=weights\|labels`) | The file a paired device pulls |
| DELETE | `/api/models/{name}` | admin | Delete a model |
| POST | `/api/models/{name}/deploy` | admin | Tell one device to use this model |
| GET | `/api/detector/local_models` | session | Hand-copied drop-folder models |
| POST | `/api/detector/local_models/select` | admin | Run a drop-folder model |
| POST | `/api/detector/local_models/delete` | admin | Delete a drop-folder HEF |
| GET | `/api/detector/builtin` | session | Built-in catalogue, and what is downloaded and selected |
| POST | `/api/detector/builtin/select` | admin | Point the local app at one built-in model |
| POST | `/api/detector/builtin/select_many` | admin | Run several models on one camera |
| POST | `/api/detector/builtin/fetch` | admin | Start a background HEF download |
| GET | `/api/detector/builtin/fetch` | session | Download progress |

Cameras, zones and the app's runtime configuration:

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/detector/control` | session | Full detector status: camera, model, zones, `running`, `stalled`, `fps`, `editable` |
| POST | `/api/detector/control/power` | admin | **Start or stop the local detection app** |
| GET / POST | `/api/detector/control/tracker` | session / admin | Tracking knobs and their allowed ranges |
| GET / POST | `/api/detector/control/params` | session / admin | Per-model filter params and emittable classes; also what the dashboard's AI model classes card saves through (`models.select`) |
| GET / POST | `/api/detector/control/camera` | session / admin | Read the camera list; point one camera at a source |
| POST | `/api/detector/control/cameras` | admin | Replace the whole camera list |
| POST | `/api/detector/control/cameras/add` | admin | Append one camera |
| POST | `/api/detector/control/cameras/enable` | admin | The per-camera on/off switch |
| POST | `/api/detector/control/cameras/remove` | admin | Drop one camera |
| GET | `/api/detector/control/cameras/resolutions` | session | Probe a source's supported sizes |
| POST | `/api/detector/control/zones` | admin | Save zones and lines |
| GET / POST | `/api/detector/control/capture` | session / admin | The snapshot and record switches |
| GET / POST | `/api/detector/control/hud` | session / admin | Which HUD rows are burned into frames |
| GET / POST | `/api/detector/control/outputs` | session / admin | GPIO, MQTT and Modbus output actions |
| GET | `/api/detector/rtsp_scan` | session | Sweep the subnet for RTSP hosts on :554 |
| GET | `/api/detector/cameras` | session | Discoverable USB and GigE cameras |

Evidence, datasets and the guided tour:

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/detector/snapshots` | session | Stored snapshots, newest first |
| GET | `/api/detector/snapshots/{sid}` | session (`?thumb=1`) | One snapshot JPEG or thumbnail |
| DELETE | `/api/detector/snapshots/{sid}` | admin | Delete one snapshot |
| POST | `/api/detector/snapshots/delete` | admin | Bulk delete |
| GET | `/api/detector/clips` | session | Recorded clips |
| GET | `/api/detector/clips/{name}` | session | One clip file |
| POST | `/api/detector/clips/delete` | admin | Delete clips |
| GET | `/api/detector/dataset` | session | Dataset capture listing and caps |
| POST | `/api/detector/dataset/repair` | dataset.capture | "Try again" on the Storage is not writable card: one forced repair of the box's folders on `/mnt/nvme` through the root helper, then the dataset check again (`core/routes/storage_repair.py`) |
| POST | `/api/detector/dataset/snap` | admin | Capture a dataset still |
| POST | `/api/detector/dataset/record` | admin | Start a dataset clip recording |
| POST | `/api/detector/dataset/record/stop` | admin | End the recording and keep the clip |
| POST | `/api/detector/dataset/delete` | admin | Delete dataset files |
| POST | `/api/detector/dataset/export` | admin | ZIP the selected files |
| GET | `/api/detector/dataset/{kind}/{name}` | session | One dataset image or clip |
| GET / POST | `/api/detector/tutorial` | session / admin | Guided-setup progress; advance or end the tour |

Board I/O:

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/board/io` | session | Board I/O inventory and live input levels |
| GET | `/api/board/ports` | session | USB sockets and ethernet jacks with live state |
| POST | `/api/board/io/test` | **operator** | Pulse an output to prove a wire |

The last row is the only `_require_operator` gate outside `hub.py`, and it is the right one:
proving which relay a wire reaches is a job for the person standing in front of the panel.

### 4.6 Video and streaming

| Method | Path | Gate | Behaviour |
|---|---|---|---|
| GET | `/video_feed/{host_serial}` | session, via `PROTECTED_PREFIXES` and `_require_session` | Relays a **paired device's** MJPEG. The device URL and bearer token come from `app.state`, never from the caller; the upstream is read with a 10 s timeout and re-emitted as a `StreamingResponse` in 4 KB chunks, defaulting to `multipart/x-mixed-replace; boundary=frame`. Device unreachable → 502 |
| GET | `/api/detector/preview` | session | Relays the **local** detection app's stream from `127.0.0.1:<stream_port>`. `fps` clamps to 1–30 and `quality` to 20–90, so a wall of tiles costs less than a single full-rate view. Retries up to four times on an upstream 503 (viewer eviction). Upstream 404 → 404 "camera not configured"; connect failure → 502 naming the port |
| GET | `/api/detector/snapshot` | session | One JPEG from a candidate source. A capture failure is **409**, never 500 - the camera being busy is an expected answer, not a server fault |
| GET | `/api/detector/webrtc` | session | Whether this camera can be watched over WebRTC now: `mode`, `configured`, `gateway`, `ready`, `path` |
| POST | `/api/detector/webrtc/whep` | session | Proxies the WHEP SDP offer to the loopback-bound gateway; **201** with `application/sdp`. Empty body → 400 |

The split matters: `/video_feed/{host_serial}` is the *device* relay and needs the pairing token;
`/api/detector/preview` is the *local app* relay and needs only the loopback port. Both are behind
the session wall, and successful requests to `/video_feed` are suppressed from the access log
(§1) because a stream is one request that never ends.

### 4.7 Devices, folders and monitoring

| Method | Path | Gate | Purpose |
|---|---|---|---|
| POST | `/api/settings/scan` | admin | LAN sweep for pairable devices |
| POST | `/api/settings/slaves` | admin | Pair a device: write the credential, poll once, audit |
| DELETE | `/api/settings/slaves/{host_serial}` | admin | Forget a device; `revoke=true` also unpairs on the device itself |
| GET | `/api/devices` | session | Live device feed for search and the dashboard cards |
| PATCH | `/api/devices/{host_serial}` | admin | Edit master-owned metadata (name, description, notes, folder), audited |
| POST | `/api/folders` | admin | Create a folder |
| PATCH | `/api/folders/{folder_id}` | admin | Rename or re-parent |
| DELETE | `/api/folders/{folder_id}` | admin | Delete a folder |
| POST | `/api/devices/{host_serial}/folder` | admin | Assign a device to a folder |
| GET | `/api/monitoring/live` | session | Live Monitoring table rows |
| GET | `/api/monitoring/bandwidth` | session | Fleet bandwidth series from the in-memory ring |
| GET | `/api/cameras/usage` | session | Which camera IPs are already assigned to another device |
| GET | `/api/control/{host_serial}` | session | A device's control snapshot (Contract v1.2) |
| POST | `/api/control/{host_serial}/{domain}` | admin | Apply device settings; 409 on a camera-IP conflict unless `_force` |
| POST | `/api/control/{host_serial}/action/{name}` | session, **admin if the device flags the action `destructive`** | Run a device action |

That last gate is re-checked against a freshly fetched snapshot rather than a cached one: whether
an action is destructive is the *device's* claim, and acting on a stale copy of that claim is how
a destructive action slips through an operator session.

### 4.8 Footage relay

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/footage/{host_serial}` | session | List a device's footage (`from`, `to`, `limit`) |
| GET | `/footage/{host_serial}/{footage_id:path}` | session | Fetch one item |
| POST | `/footage/{host_serial}/export` | admin | Relay a ZIP export of the selected footage |
| POST | `/footage/{host_serial}/delete` | admin | Relay a delete to the device |

### 4.9 Events, data tables and media

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/events` | session | Query the event store: filters, `from`/`to`, order, paging |
| GET | `/api/events/aggregate` | session | Grouped aggregation for a chart widget |
| GET | `/api/events/attributes` | session | Distinct filter values, columns, aggregation vocabulary |
| PATCH | `/api/events/{event_id}` | admin | Set status or notes on an event; **503** when Postgres is down |
| GET | `/api/datasets` | session | Table definitions, column types, caps |
| POST | `/api/datasets` | admin | Replace the table registry |
| GET | `/api/datasets/status` | session | Store reachability and per-table row stats |
| POST | `/api/datasets/name` | admin | Rename this system's table collection (a label only) |
| GET | `/api/datasets/{key}/rows` | session | Rows for one table |
| POST | `/api/datasets/{key}/rows` | admin | Insert a row by hand |
| PATCH | `/api/datasets/{key}/rows/{row_id}` | admin | Edit cells on one row |
| POST | `/api/datasets/{key}/rows/delete` | admin | Flag rows deleted |
| GET | `/api/datasets/{key}/aggregate` | session | Grouped aggregation over a table |
| GET | `/api/datasets/{key}/values` | session | Distinct values per text column |
| GET | `/api/datasets/sql/catalog` | admin | Which views the SQL console may read |
| POST | `/api/datasets/sql` | admin | Run **one read-only SELECT** against scoped views |
| GET | `/api/media` | session | Uploaded footage list, size cap, allowed extensions |
| POST | `/api/media` | admin | Upload a clip or still to run detection on |
| GET | `/api/media/{name}` | session | The file itself |
| DELETE | `/api/media/{name}` | admin | Delete an uploaded file |

The 503 on `PATCH /api/events/{event_id}` is the database degradation contract showing through:
read paths return empty when Postgres is unreachable, write paths let it propagate
([`database-schema.md`](database-schema.md)).

### 4.10 Integrations

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/blynk` | session | Masked config, push status, bindable stat keys |
| PUT | `/api/blynk` | admin | Save the Blynk config |
| POST | `/api/blynk/test` | admin | Test a token against the Blynk cloud |
| GET | `/api/email/config` | session | Masked email and SMTP config |
| POST | `/api/email/config` | admin | Save it |
| POST | `/api/email/send` | admin | Queue the digest with a cancellable delay |
| POST | `/api/email/send/cancel` | admin | Cancel a queued send |
| GET | `/api/remote` | admin | Public-URL settings, domain check, tunnel status |
| PUT | `/api/remote` | admin, audited | Save domain settings |
| POST | `/api/remote/check` | admin | Does this hostname resolve to this host? |
| POST | `/api/remote/tunnel/start` | admin, audited | Open a public tunnel |
| POST | `/api/remote/tunnel/stop` | admin, audited | Close it |

Credentials come back masked on every read; only the explicit re-auth-gated view path in
`core/services/credentials.py` ever returns a token in clear.

### 4.11 Inline on the master app

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/api/health` | **open** | The hub's probe: slug, devices online and total, `ai_running` |
| GET | `/{full_path:path}` | falls through for non-`/api` paths | The SPA shell |

Static mounts: `/static` → `core/static`, `/assets` → the built bundle, and `/system-static` →
`<systems_dir>/<slug>/static` when the system package ships one.

---

## 5. Per-system endpoints, at a glance

Three different things are easy to confuse because they all show a camera. They are separate
processes with separate gates:

| What | Where it is served | How to reach it |
|---|---|---|
| The **detection app's** MJPEG | the app itself, on `stream_port` (5102), bound loopback | `GET /api/detector/preview` on the master, which relays it |
| A **paired device's** MJPEG | the device's agent, on 5101, bearer-gated | `GET /video_feed/{host_serial}` on the master, which attaches the token |
| A **still** from any source | the master, opening the source directly | `GET /api/detector/snapshot` |

Likewise, "start detection" exists at two levels and both are real:

- `POST /api/hub/systems/{slug}/detection` on the **hub**, gated on `_require_operator` - the
  System Control switch on the grid, usable by whoever holds the system.
- `POST /api/detector/control/power` on the **master**, gated on `_require_admin` - the same
  action from inside the system's own configuration pages, where everything else is admin work.

Both call the same `core/services/detector_control` functions, off the event loop, since a stop
can block for about ten seconds.

---

## 6. Device agent - Contract v1

The agent is a separate process on port **5101**, one per paired device, and it is the only part
of the product with its own versioned, externally-implementable contract. Anyone writing an
adapter should read [`contract-v1.md`](contract-v1.md) in full; it is not duplicated here, and the
arbiter is `tools/contract_check.py`, not either document.

The surface, for orientation only:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | Liveness - unauthenticated so "device down" stays separable from "token wrong" |
| GET | `/api/agent/info` | none | Identity and capabilities: `host_serial`, `system_slug`, `api_version`, `capabilities` |
| POST | `/api/agent/pair` | pairing window | Handshake; issues the bearer token |
| GET | `/api/summary?since=<cursor>` | bearer | Events and stats since a cursor |
| GET | `/api/footage?from=&to=` | bearer | Footage list |
| GET | `/api/footage/<id>` | bearer | Footage bytes |
| GET | `/video_feed` | bearer | MJPEG |

Additive addenda, each opt-in and each declared through capabilities: **v1.1** credential pairing
and remote unpair (§10), **v1.2** the control channel the master's `/api/control/*` routes drive
(§11), **v1.3** device telemetry and credential rotation (§12).

Two rules from that document govern anything built on it. Bearer on every endpoint except the two
open ones, compared with `hmac.compare_digest`. And **no branching on `system_slug`, ever** - a
feature that needs a slug check is a feature the contract is missing
([`conventions.md`](conventions.md) §1).

---

## 7. Is there a Swagger UI?

**No.** Both servers disable it explicitly:

```python
# hub_server.py
app = FastAPI(title="IRIV Vision Studio hub", docs_url=None, redoc_url=None)

# master_server.py
app = FastAPI(title="IRIV Vision Studio master", docs_url=None, redoc_url=None,
              lifespan=_lifespan)
```

`docs_url=None` removes `/docs` and `redoc_url=None` removes `/redoc`, on both processes, on
every box. That is why this file exists: with the interactive docs switched off, the reference has
to be written down.

Switching them off is the right call for an appliance. The interactive docs are a development
convenience whose value is a form you can POST from, and on a box sitting on a customer's network
that form is a fully authenticated control panel for every admin route, rendered by machinery
nobody in this product audits. The route list is also a map: `/api/detector/control/power`,
`/api/hub/systems/{slug}` and `/api/users/{username}` are more useful to someone probing the box
than to anyone legitimately using it, who is using the SPA.

One caveat worth stating plainly rather than discovering later. `openapi_url` is **not** disabled,
so the generated schema route still exists at `/openapi.json` on both servers - and because that
path is neither under `/api/` nor in `PROTECTED_PREFIXES`, the session middleware treats it as a
client route and passes it through. It therefore answers **without a session**. Nothing sensitive
is in the schema - it is the route map and the parameter shapes, not data - but it is a route map,
and closing it means passing `openapi_url=None` alongside the two settings already there.
