# Documentation

Living document - updated as the product changes.

What each file is for, and which part of the process it belongs to. The
process itself is named in [sdlc.md](sdlc.md): iterative–incremental, Agile
Kanban-style, with continuous deployment to real hardware.

## Planning and requirements

| File | What it holds |
| --- | --- |
| [business-case.md](business-case.md) | Why the product exists, who it is for, how it is sold |
| [srs.md](srs.md) | Software requirements specification - numbered functional and non-functional requirements |
| [project-plan.md](project-plan.md) | Milestones, what is done, what is next, risks |
| [commercialization-roadmap.md](commercialization-roadmap.md) | What is built and what is not, from a product-readiness view |
| [feature-roadmap.md](feature-roadmap.md) | Feature-level plans |

## Design

| File | What it holds |
| --- | --- |
| [architecture.md](architecture.md) | Process model, data flow, config layering, storage, video, OTA layout |
| [ui-ux.md](ui-ux.md) | Page map, navigation, key screens and the decisions behind them |
| [database-schema.md](database-schema.md) | Postgres schema plus the TOML files that hold the rest of the state |
| [contract-v1.md](contract-v1.md) | The device agent contract |
| [v2-enhancement-spec.md](v2-enhancement-spec.md) | The v2 enhancement specification |

## Development

| File | What it holds |
| --- | --- |
| [conventions.md](conventions.md) | The code rules - layering, ceilings, comments, what belongs where |
| [api.md](api.md) | REST API reference for the hub, the masters and the agent |
| [features.md](features.md) | What the product does, feature by feature |
| [setup.md](setup.md) | Setting up a board and a development environment |
| [install-appliance.md](install-appliance.md) | Installing onto another IRIV box as a customer appliance, start to finish |
| [changelog/](changelog/) | One file per ISO week: every change, with a summary written at the end of the week |

## Testing and quality

| File | What it holds |
| --- | --- |
| [test-plan.md](test-plan.md) | Strategy, levels, definition of done, OTA fault drills |
| [test-cases.md](test-cases.md) | Every test file and the failure it prevents from returning |
| [bug-reports.md](bug-reports.md) | Defect log: symptom, root cause, fix, and the test that pins it |
| [security-review-2026-08.md](security-review-2026-08.md) | Security review and accepted risks |

## Keeping these true

A document that has drifted is worse than one that does not exist, because it
is believed. Two rules keep the drift bounded:

- **Every change adds its changelog line at commit time.** Not at the end of
  the week, when it would be reconstructed from memory.
- **Anything here that names a file, a route or a setting is a claim that can
  be checked.** Where a claim matters enough, it is pinned by a test in
  `tests/` rather than by good intentions - see `docs/test-cases.md`.
