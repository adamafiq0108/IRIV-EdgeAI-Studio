# Cytron-Dashboard v2.0 - Feature Enhancement Spec (planned)

> Status: PLANNED. Captured 2026-07-19 from the user's directive. To be
> implemented as its own phase AFTER the centralized-management phase
> (Contract v1.2 control channel) lands. Enhancement-only: preserve all
> existing functionality, architecture, APIs, auth, DB integrity, UI/UX,
> and business logic; backward compatible; use DB migrations; update
> DEVNOTES + API docs. Follow docs/conventions.md (routes→services→repos,
> file ceilings, capability-driven, bearer on agent endpoints, masked
> secrets, {"ok":false,"error"} shape).

## Objective
Advanced Edge Device Management for the IRIV Edge AI Platform. Scalable,
modular, production-ready foundation for future expansion. Do not redesign
or remove existing functionality unless explicitly instructed.

## 1. Edge Device Organization
Customizable hierarchical folder system; user-defined, NO hardcoded location
names; unlimited nested folders. Admin: create/rename/delete/move folder,
expand/collapse, move devices between folders. Sidebar behaves like a modern
file explorer.

## 2. Unassigned Edge Devices
A newly-paired device appears under "Unassigned Devices". Admin assigns
device name, folder, description, notes, saves - device then moves into the
chosen folder.

## 3. Edge Device Naming Management
Master remotely edits display info: device name, description, folder, notes.
Renaming NEVER affects device ID / auth / licensing / hardware identity.
Changes sync to the edge device.

## 4. Dashboard Enhancement (richer device cards)
General: device name, device ID, IP, platform version, installed apps.
System status: online/offline, health, CPU%, memory%, disk%, CPU temp,
uptime, last heartbeat. Camera: connected camera count. Auto-refresh (no
manual reload).

## 5. Device Detail Page
- General: name, ID, description, folder, hostname, IP, platform version, apps.
- System: CPU%, memory%, disk%, CPU temp, uptime, system load.
- Network: upload/download speed, daily up/down, monthly up/down.
- Camera: name, IP, status, resolution, FPS.
- AI app: running app, AI model, current FPS, inference time, detection
  count, app status.

## 6. Live Monitoring
Online/offline, API status, last heartbeat, CPU%, memory%, disk%, CPU temp,
uptime, upload/download speed. Real-time bandwidth graph (upload+download).
Auto-refresh.

## 7. Device Health Monitoring
Monitor API availability, connection status, response time, CPU temp,
resource utilization. Classify: Healthy / Warning / Critical / Offline.
Thresholds configurable.

## 8. Camera Overview
All connected cameras: name, IP, status, resolution, FPS, connection health.
Design to support future camera diagnostics.

## 9. Authentication & Credential Management (operational info only)
Show: auth status, connection status, credential status, token status, token
expiration, last credential rotation, last successful auth. Bearer token
hidden by default.

## 10. Hidden Manufacturing Information (NEVER customer-facing)
Never display: Pi serial, ATECC info, MAC, hardware identifier, provisioning
info, manufacturing info. Used internally for auth / licensing / secure comms
/ platform verification only.

## 11. Automatic Credential Rotation (atomic, zero-downtime)
On "Rotate Credentials": (1) admin verification, (2) prompt username+password,
(3) edge device generates a NEW bearer token, (4) device keeps BOTH old+new
valid temporarily, (5) master stores new token, (6) master switches to new
token immediately, (7) verify comms, (8) then instruct device to revoke the
old token, (9) remove old token, (10) success notification. Admin never
manually copies the token; comms never interrupted.

## 12. View Bearer Token (advanced troubleshooting)
Require admin verification (username+password) before reveal. Reveal / copy /
hide. Auto-hide on page refresh, leaving page, logout, timeout.

## 13. Revoke Credentials
Admin verification (username+password) + confirmation dialog. Immediately
terminates auth until a new credential is established.

## 14. Remote Device Actions (each requires confirmation)
Restart device, shutdown device, restart AI app, restart camera service,
restart platform services, refresh device info, synchronize configuration.

## 15. Configuration Synchronization (future-facing)
Master remotely syncs config to devices: camera, AI detection settings, AI
model, MQTT, network, platform, application config. No physical access needed.

## 16. Search & Filtering
Global search by device name, device ID, IP, folder, camera name. Filters:
online, offline, healthy, warning, critical, installed app, folder.

## 17. Platform Overview
Total registered / online / offline devices, total cameras, running AI apps,
current platform bandwidth, active alerts, system warnings. Auto-refresh.

## 18. Audit Log (centralized)
Record: login, logout, device registration, rename, assignment, folder
create/rename/delete, device move, config sync, restart/shutdown device,
restart app, rotate credentials, view token, revoke credentials. Fields:
timestamp, username, device, action, result, IP (optional). Support future
export.

## 19. Database Enhancement
Normalized folder hierarchy via parent-child (NO static path strings).
Devices reference folders by foreign key. Folder rename/move and device move
must not update device records. DB migrations for all schema changes.

## 20. UI Enhancement
Preserve existing layout; sidebar = modern file explorer (expand/collapse,
nested folders, device selection, device move, responsive). Clean, modern,
professional.

## 21. Security
Bearer tokens encrypted at rest; never logged; never exposed via unsecured
APIs; hardware identifiers hidden; sensitive credential ops require admin
verification; credential rotation atomic; comms uninterrupted during
rotation; old creds revoked only after successful verification; all
security actions audited.

## 22. Development Requirements
Preserve existing functionality + MVC; reuse existing services/shared
components; no needless duplication; backward compatible; follow project
structure + standards; DB migrations; update REST API docs; update DEVNOTES
(architecture / schema / API / security / features / config). Thoroughly
test; production-ready. Only implement requested enhancements.

---

### Notes for the implementer (mapping to what already exists)
- Much telemetry (CPU/mem/disk/temp/uptime/network/camera/AI-app stats) must
  be SUPPLIED BY THE DEVICE - extend the agent adapters' `stats()` and/or add
  a `control.telemetry`/heartbeat surface; the master only aggregates.
- Credential rotation (#11) builds directly on Contract v1.1/v1.2: the agent
  already mints tokens (pairing v2) and has `/api/agent/unpair`; add a
  keep-both-valid rotation step to `shared/pairing_store.py` (token_hash +
  a `pending_token_hash` grace slot) and a master rotate flow reusing
  `settings.revoke_on_device`.
- Hardware identity hiding (#10) aligns with `shared/licensing` +
  `device_identity` - expose activation_key/status, never cpu/atecc serial.
- Folder hierarchy (#1, #19): new master-side tables + repos in Cytron
  (`core/repos/` is currently empty/aspirational - this is where it lands),
  device→folder FK, folder tree in the sidebar. Master state is in-memory
  today; this phase likely activates the Postgres `cytron_dashboard` DB.
- Audit log (#18) mirrors the device-side `auth_events` pattern; master needs
  its own audit table.
