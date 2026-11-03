# Cytron System Contract v1

The single source of truth for how an AI system plugs into the Cytron Dashboard.

A system that serves these endpoints gets the full dashboard - hub card, Main, Video Stream,
Footage, Settings, Email - with **zero dashboard code written for it**. Per-system code is
limited to analytics widgets and declared extras.

This contract is executable. `tools/contract_check.py` is the arbiter; this document explains it.
If the two disagree, the harness is right and this file is a bug.

Status: **internal**. Versioned via `api_version`, but no public semver or deprecation policy yet.

---

## 1. The agent

Every system runs an **agent** on port **5101**, as its **own process**, separate from the
detection app.

The separation is load-bearing: detection apps get killed and relaunched (by updates, by
operators, by crashes). The agent must stay up through all of it so the master can report
"detection stopped" instead of the device just vanishing from the dashboard.

The agent never owns detection logic. It is a read-only window onto whatever the system
already records, plus a proxy for its video.

---

## 2. Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | liveness |
| GET | `/api/agent/info` | none | identity + capabilities |
| POST | `/api/agent/pair` | pairing window | handshake, issues bearer token |
| GET | `/api/summary?since=<cursor>` | bearer | events + stats since cursor |
| GET | `/api/footage?from=&to=` | bearer | footage list |
| GET | `/api/footage/<id>` | bearer | footage bytes |
| GET | `/video_feed` | bearer | MJPEG stream |

### GET /api/health

Unauthenticated on purpose - the master must distinguish "device down" from "token wrong".

```json
{"ok": true, "api_version": "1.0", "uptime_s": 84213}
```

### GET /api/agent/info

```json
{
  "host_serial": "10000000abcd1234",
  "system_slug": "conveyor",
  "display_name": "Handrail Detection",
  "api_version": "1.0",
  "capabilities": ["stream", "footage", "snapshots", "analytics.daily", "email"],
  "camera": {"label": "USB Camera 0", "running": true},
  "app_running": true,
  "paired": true
}
```

`host_serial` - the Pi CPU serial, canonical device identity. See §5.
`system_slug` - matches the deployment's system package folder name.
`api_version` - `"MAJOR.MINOR"`. See §7.

### POST /api/agent/pair

Request: `{"master_serial": "...", "token": "..."}` → `{"ok": true, "host_serial": "..."}`

The agent accepts a pair request **only** when unpaired, or within a short operator-opened
unlock window. Otherwise any host on the LAN can seize a device. Once paired it stores
`{master_serial, token}` and requires the token everywhere below.

Compare tokens with `hmac.compare_digest`, never `==`.

### GET /api/summary?since=&lt;cursor&gt;

The core endpoint. Everything on Main and Analytics comes from here.

```json
{
  "host_serial": "10000000abcd1234",
  "system_slug": "conveyor",
  "api_version": "1.0",
  "now": "2026-07-15T10:04:11+08:00",
  "next_cursor": "8842",
  "events": [ ... ],
  "stats": { ... }
}
```

`since` omitted ⇒ start from the beginning. The master persists `next_cursor` per slave and
replays from it, so a master outage costs nothing: data waits on the slave and backfills on
reconnect.

**Cursor rules** - get these wrong and you get duplicate or lost events:

- Opaque string to the master. Never parse it there.
- Monotonic. Never reused, never reordered.
- Exclusive: `since=X` returns events strictly after `X`.
- Stable across agent restarts. Derive it from durable storage, not memory.
- `next_cursor` is the last returned event's cursor. No events ⇒ echo `since` back.

Page freely; the master keeps calling until `events` is empty.

`now` is mandatory. The master computes `skew = master_now - slave_now` and flags outliers.
Pis have no RTC - a device that boots without NTP timestamps events in 1970 and would
silently poison the digest.

### GET /api/footage, GET /api/footage/&lt;id&gt;

List returns metadata only. Footage stays on the device that recorded it; the master streams
bytes on demand and never copies files.

```json
{"items": [{"id": "hr-snap-8842", "ts": "...", "type": "violation",
            "device": "cam1", "bytes": 148213, "mime": "image/jpeg"}]}
```

### GET /video_feed

`multipart/x-mixed-replace; boundary=frame`. The agent proxies the detection app's existing
MJPEG. Cameras don't get opened twice - that fails on most hardware.

---

## 3. The event envelope

Generic pages read **only** these fields. This is the whole reason one dashboard serves every
system.

```json
{
  "id": "hr-8842",
  "ts": "2026-07-15T10:00:00+08:00",
  "type": "violation",
  "severity": "warn",
  "label": "not_holding",
  "device": "cam1",
  "snapshot_id": "hr-snap-8842",
  "detail": {"direction": "up", "risk_score": 0.82}
}
```

| Field | Req | Notes |
|---|---|---|
| `id` | yes | unique within the system; prefix with the slug |
| `ts` | yes | ISO 8601 **with offset**. Naive timestamps are rejected. |
| `type` | yes | `violation` \| `info` \| `alert` \| `system` |
| `severity` | yes | `ok` \| `warn` \| `crit` |
| `label` | yes | short human string, e.g. `not_holding` |
| `device` | no | camera/sensor id within the system |
| `snapshot_id` | no | resolves against `/api/footage/<id>` |
| `detail` | no | free-form object |

`detail` is the escape hatch. Anything system-specific goes there and renders as a key/value
table. **Never add a top-level field for one system's needs** - that is how a shared contract
rots into a union of every customer's requirements.

`type` and `severity` are closed sets because generic pages filter, colour, and count by them.
`label` is open because nothing branches on it.

---

## 4. stats

Standard keys - all optional, but a system serving none gets an empty Main page:

| Key | Type |
|---|---|
| `events_today` | int |
| `events_total` | int |
| `violations_today` | int |
| `violations_total` | int |
| `uptime_s` | int |
| `camera_running` | bool |
| `camera_label` | string |

Extra keys are allowed and render generically as tiles. Same rule as `detail`: extend here, not
in the envelope.

---

## 5. Identity

```bash
cat /sys/firmware/devicetree/base/serial-number
```

Devicetree strings are **null-terminated** - strip the trailing `\x00` and lowercase before use,
or the serial will silently mismatch. Fall back to `/proc/cpuinfo` `Serial:` on non-devicetree
hosts.

Operator-facing labels are display only and never a key. Two devices can carry the same label;
they cannot carry the same CPU serial.

Don't confuse this with camera serials, which are a different namespace. Name variables
`host_serial` and `camera_serial` - never bare `serial`.

---

## 6. Capabilities

Nav and features derive from `capabilities`. Nothing in the dashboard branches on
`system_slug`; if you ever need to, the contract is missing a capability.

| Capability | Unlocks |
|---|---|
| `stream` | Video Stream page, `/video_feed` |
| `footage` | Footage page |
| `snapshots` | thumbnails on events |
| `analytics.daily` | daily/hourly charts |
| `analytics.direction` | direction breakdown |
| `settings.zones` | zone editor |
| `email` | Email Setup page |
| `mqtt` | MQTT status panel |

A system omitting `email` simply has no Email nav item. No code, no flag, no special case.

---

## 7. Versioning

`api_version` is `"MAJOR.MINOR"`, present in `/api/health`, `/api/agent/info`, and every
summary response.

- **MINOR** - additive only: new capability, new optional field, new `stats` key. Master ignores
  what it doesn't know.
- **MAJOR** - anything else. The master **refuses** the slave and reports it, rather than
  half-ingesting garbage.

Master supports the current major and reports mismatches on the hub card.

---

## 8. Rules

Do:
- Serve read-only. The agent reports what the app already recorded.
- Derive cursors from durable storage.
- Put system-specific data in `detail` and `stats`.
- Return `now` in every summary.
- Emit ISO timestamps with an offset.

Don't:
- Add a top-level envelope field for one system.
- Branch on `system_slug` anywhere in the dashboard.
- Let the master parse a cursor.
- Copy footage to the master.
- Open the camera in the agent.
- Reuse or reorder a cursor.

---

## 9. Conformance

```bash
python tools/contract_check.py http://<device>:5101 --token <token>
```

Runs against `tools/mock_slave.py` too, so dashboard work needs no hardware.

A new adapter is done when the harness passes **unedited**.

---

## 10. v1.1 addendum (additive -- v1 clients and agents keep working)

`api_version` is now `"1.1"`. Two additions, both optional:

### Credential pairing (v2 flow)

`POST /api/agent/pair` may carry the DEVICE's platform admin credentials
instead of a master-generated token:

```json
{"master_serial": "...", "username": "...", "password": "..."}
```

Only available when the agent was started with `--auth-backend
module:callable` (the deployment supplies the credential check -- e.g. the
IRIV platform's `shared.pairing_store:pairing_auth_backend`). The agent
validates the credentials, mints the token itself and RETURNS it:

```json
{"ok": true, "host_serial": "...", "token": "..."}
```

Responses: `401` bad credentials (5 failures from one source within 15
minutes → `429` lockout), `409` already paired (single-master rule outranks
credentials), `400` when no auth backend is configured. No unlock window is
needed for an unpaired device. Bodies WITHOUT `username`/`password` follow
the v1 flow unchanged (master-supplied token + unlock window).

### Remote unpair

`POST /api/agent/unpair` (bearer-token gated) severs the pairing: the token
is revoked immediately and the device returns to waiting-for-pairing.
`/api/health` and `/api/agent/info` keep answering (`"paired": false`);
every token-gated endpoint answers `401`. The master's Settings page calls
this on Remove (`?revoke=false` to skip); the device's own dashboard may
call the same store directly ("Disconnect Master").

Deployments may also replace the pairing state backend with `--pairing-store
module:factory` (e.g. a database-backed store where revocation is effective
on the next request with no agent restart). The factory returning `None`
falls back to the file store.

---

## 11. v1.2 addendum - control channel (additive, opt-in)

`api_version` is now `"1.2"`. Contract v1 stays read-only by default; v1.2
adds an OPT-IN control surface for deployments that want the master to
manage devices. A slave without `control.*` capabilities is still fully
conforming - the checker skips the section.

### Declaring control

The adapter implements the optional `control_*` methods on
`agent.adapters.base.Adapter` AND declares one capability per domain:
`control.device`, `control.mqtt`, `control.camera`, `control.storage`,
`control.detection` (extend this list here when adding a domain). A domain
is served only when both are true.

### Endpoints (all bearer-gated)

```
GET  /api/control                  {ok, api_version, domains:{name: envelope}}
POST /api/control/{domain}         apply settings -> {ok, domain, ...envelope}
POST /api/control/action/{name}    run action     -> {ok, ...result}
```

Envelope per domain:

```json
{"settings": {...}, "status": {...},
 "actions": [{"name": "device.reboot", "label": "Reboot",
              "destructive": true, "params": []}],
 "meta": {...}}
```

### Rules

- `destructive: true` is what drives confirmation UX on the master -
  never action-name matching.
- Action names are domain-prefixed (`device.reboot`, `storage.purge`,
  `camera.scan_usb`, `mqtt.test`, `app.start`).
- Reboot-class actions must RESPOND before executing (schedule, then act):
  `{"ok": true, "scheduled": true}`.
- Secrets in `settings` are masked on read; a slave only overwrites a
  secret when the client sends a non-masked value.
- Unknown domain or action → 404; invalid payload/params → 400; the error
  shape stays `{"ok": false, "error": ...}`.
- The master renders domains generically from the envelope - it must not
  branch on domain names beyond layout niceties, so a new domain costs
  zero master code.

---

## 12. v1.3 addendum - device telemetry (additive, optional)

`api_version` is now `"1.3"`. `GET /api/summary` may carry a top-level
`telemetry` object alongside `stats`:

```json
"telemetry": {
  "cpu_percent": 12.4, "mem_percent": 38.0, "disk_percent": 61.2,
  "cpu_temp_c": 47.8, "load_avg": [0.3, 0.2, 0.1],
  "net_up_bps": 10240, "net_down_bps": 40960,
  "platform_version": "1.0.0", "installed_apps": ["conveyor_detection"]
}
```

Rules:
- Telemetry rides its OWN field, **never `stats`**. The master sums `stats`
  keys across the fleet; a summed CPU%/temperature is meaningless. Fleet
  bandwidth is a deliberate sum computed by the master's overview service,
  not an auto-sum of a stats key.
- Every metric is optional and best-effort: an unavailable metric is simply
  absent (the adapter never fails a summary over telemetry). An adapter that
  doesn't implement `telemetry()` returns `{}` and is fully conforming.
- The master stamps `last_heartbeat` itself on a successful summary; devices
  need not send it.

Deployment note: the reference implementation is `shared/telemetry.py`
(psutil, with `/sys/class/thermal` and `vcgencmd` fallbacks for CPU temp) in
the deployment repo; the agent venv installs `psutil`.

### v1.3 credential rotation endpoints (agent)

Zero-downtime rotation (keep-both-valid), both bearer-gated:

```
POST /api/agent/rotate          -> {ok, token}   mint a new token, store it
                                                  PENDING (old stays valid)
POST /api/agent/rotate/commit   -> {ok}          promote pending; old dies
```

Flow the master runs: rotate → switch to the new token → verify a summary
call succeeds with it → commit. If it never commits, the old token still
authenticates and the next rotate overwrites the stale pending one - no
401 gap and no lockout. `require_token` accepts the current OR the pending
token throughout the window.

Hardware identity note (#10): the only device identifier on the wire is
`host_serial` (the fleet ID). CPU serial, ATECC serial and MAC are used
only on-device for licensing/auth and are never exposed by the agent or
returned by any master endpoint.
