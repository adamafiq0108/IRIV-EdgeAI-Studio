# Commercialization Roadmap

Status: planning handoff. This document converts the current codebase into a
commercial/open-source product plan and gives Claude Code a concrete update
brief. It should be used together with `docs/v2-enhancement-spec.md`,
`docs/conventions.md`, and `docs/contract-v1.md`.

## Current Product Shape

Cytron Dashboard is already positioned well for commercialization:

- generic FastAPI backend and React/Vite SPA
- one hub, many independent system masters
- device integration through a contract instead of dashboard forks
- demo system that runs without hardware
- auth, audit, logs, builder, model upload/deploy, remote access, folder/device
  metadata, health monitoring, and CI contract checks

The strongest business angle is this:

> "A reusable edge-AI operations dashboard where developers plug in devices by
> implementing a small contract, and non-technical operators manage monitoring,
> cameras, credentials, models, and device actions through a browser."

## Main Gaps Before Commercial Release

### 1. Operator Experience

The dashboard has useful capability, but some workflows still feel like a dev
tool:

- sensitive actions use `prompt`, `confirm`, and `window.prompt`
- Manage page renders control domains generically but lacks a polished device
  detail layout
- search/filter is local to Platform rather than global
- empty states, permission states, and offline states need consistent treatment
- sidebar folder operations should feel like a file explorer, including move,
  rename, create, delete, collapse, and selected-device context

### 2. Open-Source Readiness

The README says the license is not chosen yet, while the stated goal is open
source. Before public release:

- choose a real license: Apache-2.0 is a strong default for commercial-friendly
  OSS, MIT is simpler, AGPL is stronger copyleft
- add `docs/quickstart.md` for non-experts
- add screenshots or GIFs
- add a "build your first system" tutorial
- add an example prompt pack for AI-assisted customization
- document extension boundaries clearly: product repo vs deployment repo

### 3. Commercial Readiness

Industry users usually expect:

- user roles beyond admin/operator, or at least clear RBAC hooks
- backup/restore of config, model manifests, folder metadata, and audit logs
- exportable reports: CSV/PDF for events, audit, devices, health
- alert rules and notification channels
- update/version management for agents and deployed models
- onboarding wizard for first system, first device, and first model
- deployment hardening checklist: TLS proxy, firewall, secrets, database,
  systemd, backup, log retention

### 4. Developer Experience

The project already has a good contract and demo system. Improve adoption with:

- a generated starter system package
- a stricter contract compatibility page in the UI
- schema examples for `stats`, telemetry, control domains, and widgets
- CLI helpers for `new-system`, `check-system`, `export-system`
- API documentation generated from FastAPI OpenAPI plus hand-written guidance

## Recommended Product Phases

### Phase 1: Polish The Existing V2 Surface

Goal: turn the current dashboard from "powerful demo" into "operator-ready".

Update areas:

- `frontend/src/pages/Credentials.jsx`
  Replace browser prompts with modal dialogs using existing UI components.
  Add reveal/hide token state, copy button, auto-hide on route change, and a
  clear re-auth step.

- `frontend/src/pages/Control.jsx`
  Split the Manage page into tabbed sections: Overview, Camera, AI App,
  Storage, Actions, Raw Capabilities. Keep the generic domain renderer for
  fallback domains.

- `frontend/src/pages/Platform.jsx`
  Keep search/filter, but add installed-app and folder filters. Make rows link
  clearly to the device detail/manage page.

- `frontend/src/pages/Monitoring.jsx`
  Add health summary tiles, response time/API status, and clearer offline
  handling. Keep the current polling model.

- `frontend/src/components/Sidebar.jsx`
  Ensure nested folders, unassigned devices, selected folder scope, and admin
  mutations are complete and ergonomic.

- `core/services/health.py`, `core/services/monitoring.py`
  Verify health classification uses configurable thresholds and returns enough
  reason data for UI badges/tooltips.

Acceptance checks:

- `npm run build` in `frontend/`
- existing CI commands still pass
- no secrets or hardware identifiers appear in UI/API responses
- no direct SQL in routes/services

### Phase 2: Open-Source Launch Package

Goal: make a new user successful in less than 30 minutes without hardware.

Update areas:

- `README.md`
  Reframe as a product landing README: what it is, who it is for, screenshots,
  quickstart, architecture, extensibility, security, license.

- `docs/setup.md`
  Split advanced deployment details from a beginner quickstart.

- `docs/contract-v1.md`
  Add "minimal adapter" and "telemetry example" sections.

- `docs/features.md`
  Convert from internal feature report into public feature catalog.

- new `docs/ai-customization-guide.md`
  Explain how a user can prompt an AI agent to create a new system package,
  add widgets, and implement an adapter without editing core dashboard code.

- `.github/ISSUE_TEMPLATE`, `.github/PULL_REQUEST_TEMPLATE`
  Ensure the templates guide contract-safe contributions.

Acceptance checks:

- fresh clone works from README only
- mock slave contract check passes
- docs do not mention private customers, private hosts, or hidden hardware IDs

### Phase 3: Industry Features

Goal: add common features that buyers expect without damaging the generic
contract model.

Prioritized features:

1. Alerts and notifications
   - configurable rules: offline, critical health, high temp, disk full,
     app stopped, model deploy failed
   - channels: email first, webhook later
   - files: `core/routes/alerts.py`, `core/services/alerts.py`,
     `core/repos/alerts.py`, `frontend/src/pages/Alerts.jsx`

2. Reports and exports
   - CSV export for devices, events, audit, health
   - scheduled email report reuse
   - files: `core/routes/reports.py`, `core/services/reports.py`,
     `frontend/src/pages/Reports.jsx`

3. Backup and restore
   - export/import config snapshot excluding secrets by default
   - optional encrypted secret backup
   - include model manifests, dashboard builder layouts, folder metadata

4. Agent/model version management
   - show agent version, app version, model version, config version
   - flag outdated devices
   - support staged model rollout by folder/device

5. Onboarding wizard
   - first admin setup
   - add system
   - pair first device
   - choose model
   - verify live monitoring

### Phase 4: Commercial Packaging

Goal: support both open-source use and paid/professional deployments.

Recommended split:

- Community edition:
  contract dashboard, demo system, monitoring, model management, builder,
  local auth, audit, logs, docs

- Commercial services/add-ons:
  hosted support, deployment templates, enterprise auth integration, backup
  automation, managed updates, support SLA, white-label branding, advanced
  alerting connectors

Avoid hardcoding license enforcement into the product until the OSS strategy is
settled. Keep commercial value around packaging, support, integrations, and
deployment reliability.

## Claude Code Update Brief

Use this brief when asking Claude Code to make the next implementation pass:

```text
You are working in D:\Cytron-Dashboard.

Read first:
- docs/conventions.md
- docs/contract-v1.md
- docs/v2-enhancement-spec.md
- docs/commercialization-roadmap.md
- README.md

Goal:
Make the existing v2 dashboard more commercial/operator-ready without changing
the core architecture. Preserve product-vs-deployment separation, contract-first
device integration, routes -> services -> repos layering, and backward
compatibility.

First implementation target:
Replace the browser prompt/confirm flows in Credentials and Manage with
proper React dialogs using the existing UI component set. Keep APIs unchanged
unless a small additive response field is needed.

Update these parts:
- frontend/src/pages/Credentials.jsx
  Use Dialog/Input/Button components for admin re-auth, token reveal/copy,
  rotate confirmation, and revoke confirmation. Never render the token until
  re-auth succeeds. Auto-hide token on close/unmount.

- frontend/src/pages/Control.jsx
  Replace prompt/confirm usage for actions and rename with dialogs. For
  destructive actions, require typed confirmation. Preserve generic rendering
  of unknown control domains.

- frontend/src/components/ui/*
  Reuse existing components. Do not introduce a new UI library.

- core/services/audit.py and credential/control routes only if needed
  Ensure view-token, rotate, revoke, rename, and action execution are audited.
  Do not expose bearer tokens in logs, errors, or bootstrap.

Quality requirements:
- npm run build in frontend must pass
- no prompt(), alert(), or confirm() should remain in Credentials.jsx or
  Control.jsx
- no SQL in routes/services
- no hardware/manufacturing identifiers added to customer-facing UI
- keep changes scoped; do not redesign the whole dashboard in this pass
```

## Next Planning Decision

The best immediate next step is Phase 1, starting with replacing prompts and
confirm dialogs in `Credentials.jsx` and `Control.jsx`. That improves perceived
quality, security posture, and non-technical usability without needing backend
schema changes.
