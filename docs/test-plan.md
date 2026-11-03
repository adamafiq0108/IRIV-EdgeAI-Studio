# Test plan

Living document - updated as the product changes.

## What is being protected

This product runs unattended, on hardware in someone else's building, watching
something that matters to them. The failures that hurt are not crashes - a
crash is loud, and systemd restarts it. The failures that hurt are the quiet
ones: a system that reports healthy while its camera shows the wrong feed, a
class filter that is silently dropped so everything gets tracked, a zone that
was disabled at build time and can never be switched back on.

Every one of those shipped once. The suite exists to stop them coming back.

## Strategy: tests as contract

`tests/smoke_*.py` are not unit tests chasing coverage. Each file pins an
invariant, and its header comment names the failure it prevents. Three kinds:

**Behavioural.** Drive the real module and assert what comes out - the
workflow compiler, the function engine, the tracker, zone logic, the store's
install pipeline.

**Source assertions.** Read the source and assert a structural fact: that
`core/services/functions.py` TYPES still equals the app's TYPES (they are
separate copies, and drift makes a node the palette offers fail to save);
that every route in `core/routes/update.py` calls `_require_admin`; that the
applier imports nothing from `core.`. These catch defects that no runtime
test would, because the failure only appears on a machine the test cannot be.

**Cross-copy pinning.** The detection app deliberately shares no code with
`core/`. Where logic is duplicated on purpose, a test asserts the copies still
agree.

Running: `python tests/run_all.py` from the repository root. It discovers
`tests/smoke_*.py` (plus the JS checks) automatically - a new test file needs
no registration. About forty seconds; that is a deliberate budget, because a
slow gate gets skipped.

## Levels

| Level | What runs | When |
| --- | --- | --- |
| Static | `tests/check_undefined_names.py`, `tests/check_jsx_refs.mjs`, CI's convention checks (no SQL in routes, line ceilings, no slug branching) | every suite run, every push |
| Component | the smoke suite | before every commit |
| Build | `npm run build` (owner) / `build:customer` | before every commit touching the frontend |
| Contract | `tools/contract_check.py` against the mock device and the reference adapter | CI |
| System, on hardware | deploy to the board, restart the stack, watch live cameras | before every change is called done |
| Release | the full suite again, plus signature verification of the built bundle | on every tag (`.github/workflows/release.yml`) |

## Definition of done

A change is done when all of these are true. Not most.

1. The full smoke suite passes.
2. The frontend builds.
3. It is committed on `cytron_iriv`.
4. It is deployed to the board and the stack is restarted.
5. It has been watched working on the real hardware - the actual page, the
   actual camera, the actual detection.
6. If it fixes a defect, a test pins it. If it adds a feature, a test pins the
   part that would be silently wrong.
7. One line is appended to the current week's changelog.

## OTA fault drills

The update path cannot be signed off by a happy-path install. Before a release
goes to a customer box, these are run deliberately on a test board:

| Drill | Expected |
| --- | --- |
| Tamper with the manifest between publish and install | refused before the bundle is downloaded; nothing on disk changes |
| Cut power during the download | staging discarded or resumed; the live tree was never touched |
| Cut power during the symlink flip | `irivai-recover.service` restores the previous release at boot |
| Fill the store volume | preflight refuses with a readable message |
| Point at a release needing a newer Hailo driver | preflight refuses and names the driver |
| Kill the new hub during gate A | automatic rollback; the old version is serving again |
| Break a detection app in the new release | gate B fails; automatic rollback |
| Press Update twice | the second attempt gets 409, not a second updater |
| Roll back, then update again | both directions work; data and config survive both |

After every drill: `config/*.local.toml` intact, `config/deploy.toml` still
`customer`, every system's data and models untouched, and the release token
absent from logs, from `status.json`, and from `ps`.

## What is deliberately not tested automatically

Anything that needs the Hailo device, a camera, or a network link is verified
by hand on the board. Mocking those would test the mock. The suite's job is to
make the hardware check short enough that it actually gets done.
