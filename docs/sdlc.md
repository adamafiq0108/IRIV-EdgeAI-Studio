# How this software is built

Living document - updated as the product changes.

This file names the process. Not because a process needs announcing, but
because the rest of the documentation set only makes sense once you know
which model it belongs to: a requirements specification means something
different under a waterfall (a contract, signed before code) than it does
here (a running description of what the product does, corrected as it
changes).

## The model: iterative and incremental

IRIV Edge AI is built by evolutionary prototyping on the real hardware. Each
cycle is small - a feature, a fix, a page - and each one ends on a board with
cameras attached. Requirements are refined by *using* the thing: the motion
trail feature exists because the trail was drawn and then watched; the camera
switch moved beside the Start button because it was in the wrong place when
someone reached for it.

That is the honest description of an iterative–incremental model. There is no
phase in which requirements are frozen, and no phase in which the product is
"in testing" as a state it enters and leaves.

## The methodology: Agile, Kanban-style

Continuous flow, not timeboxes.

- **Work in progress is one item.** A request is taken, finished, verified on
  hardware, and committed before the next is started. The queue is the
  conversation, not a board.
- **No sprints, no ceremonies, no estimates.** There is no planning meeting to
  attend and no velocity to measure - the cost of the coordination would
  exceed the coordination.
- **Pull, not push.** The next item is chosen when the previous one is done,
  from whatever the deployment actually needs that day.
- **Definition of done is fixed and non-negotiable** (see below). It is the
  only ceremony there is.

Why not the alternatives:

| Model | Why not |
| --- | --- |
| Waterfall | Requirements here are discovered by running the product on real cameras. A specification signed before the first frame would describe a different product. |
| Scrum | Sprints and ceremonies assume a team coordinating across a fixed cadence. One-item flow with a same-day feedback loop gets the same discipline without the overhead. |
| V-model | Its value is a certified trace from requirement to test. This suite traces the other way - tests are written *at* the defect, pinning behaviour that broke once. |

## Engineering practices on top

**Continuous deployment.** Every change follows the same path, every time:

    build the frontend  →  run the full smoke suite  →  commit  →
    deploy to the board  →  restart the stack  →  verify on live cameras

A change that has not run on the board is not finished. This is why the suite
is fast (about forty seconds): a slow gate is a gate people learn to skip.

**Tests as contract, not coverage.** `tests/smoke_*.py` are not unit tests
chasing a percentage. Each one pins an invariant that was violated once, and
its header says which failure it prevents from returning. Several are source
assertions - that two duplicated lists still agree, that every route in a file
is gated - because the defects they catch are structural, not behavioural.
See `docs/test-plan.md`.

**Trunk-based, single working branch.** Development happens on `cytron_iriv`.
Long-lived feature branches would hide the very integration this process
depends on.

**Comments carry the reasoning.** The code says what; the comment says why,
and usually names the failure that made it necessary. This is the project's
main defence against a fix being "cleaned up" back into the bug it replaced.
See `docs/conventions.md`.

**DevOps release engineering** (from v2.1.0). Tagging a version builds a
signed release bundle in CI and publishes it to a private repository;
appliances in the field check for it and install it themselves, with rollback
and boot-time repair. This is the point at which "deployed" stops meaning
"the developer's board" and starts meaning "the fleet". See
`docs/architecture.md` and the OTA sections of `docs/srs.md`.

## Where each document belongs

| SDLC phase | Deliverable | File |
| --- | --- | --- |
| Planning & requirements | Business case / project charter | `docs/business-case.md` |
| | Software requirements specification | `docs/srs.md` |
| | Project plan, milestones | `docs/project-plan.md` |
| Design | System architecture | `docs/architecture.md` |
| | UI/UX map and wireframes | `docs/ui-ux.md` |
| | Data schema (database + config files) | `docs/database-schema.md` |
| Development | Source code documentation | in the code; rules in `docs/conventions.md` |
| | API reference | `docs/api.md` |
| | Status reports | `docs/changelog/<year>-W<week>.md` |
| Testing & QA | Test plan | `docs/test-plan.md` |
| | Test cases | `docs/test-cases.md` |
| | Defect log | `docs/bug-reports.md` |

## The weekly record

Every commit adds one line to the current week's file under
`docs/changelog/`. At the end of a week that file gets a summary written at
the top of it: what shipped, what was fixed, what is still open. The raw
lines stay underneath, because a summary that replaces its evidence is a
summary nobody can check.

This replaces daily stand-ups and status meetings. It is written as the work
happens rather than reconstructed afterwards, which is the only way such a
record is ever accurate.
