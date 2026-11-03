# Project plan

Living document - updated as the product changes.

Milestones, not a Gantt chart. The process is continuous flow
(`docs/sdlc.md`), so what this file records is what is finished, what is
being worked on now, and what is next - with the ordering reasoning that
would otherwise live only in someone's head.

## Where the product stands

**Updated 2026-09-11 (Amato)** for the beta push.

Version **2.1.0**. Feature-complete for a single-box deployment: systems,
cameras, models, zones, visual workflow, dashboards, history, accounts and
per-system access, the template store, and over-the-air updates. Two live
test boxes carry the current build: **192.168.1.234** (Hailo-8) and
**192.168.1.63** (Hailo-10H).

## Milestone: beta hardening (current, 2-week target)

Tok Aba has set a beta launch target of **2 weeks from 2026-09-11**
(target: **2026-09-25**). This is the active milestone; OTA updates (below)
is the previous one and is mostly complete.

The week just closed (2026-09-10/11) shipped 14 fixes to detection accuracy
and reliability in one day - line-counter correctness, chip contention,
dead-app reporting, video/inference throughput - found and fixed by driving
the real hardware rather than guessing. That is the working model for the
two weeks ahead: every fix verified live on .63 and/or .234, not just in the
sandbox suite.

### Open items blocking beta (as of 2026-09-11)

| # | Item | Box | Owner | State |
| --- | --- | --- | --- | --- |
| 1 | Parking counter (`z`) reads 0 - line placement vs. actual car path needs checking, and its rule is still bound to a model name (`yolov8s`) the system no longer runs | .63 | Boboiboy | open |
| 2 | 19 orphan data folders (~2MB) - listed, not deleted | .63 | Boboiboy | open, needs Tok Aba's go-ahead to delete |
| 3 | Two split store roots (`/mnt/nvme/cytron/store` vs `/home/pi/.local/share/irivai-systor`) not merged | .63 | Boboiboy | open |
| 4 | Peak Hour / Most Violate Zone analytics query - corrected SQL written, not applied to any live system | both | Boboiboy | open |
| 5 | A workflow graph on `counting_template_editor` has two nodes switched off (Class Filter, a Counter), so most of that canvas produces no cards - needs confirming this is intentional | .63 | Ying / Tok Aba | needs a decision, not a fix |
| 6 | Several systems running detection at once on .63's exclusive Hailo-10H chip is confirmed as expected behaviour, not a fault - a hard-block guard tried for this was shipped and reverted same day (`af34895` -> `d2e78ba`) | .63 | - | **resolved 2026-09-11** |

None of the open items are structural. They are known, named, and each has an owner.

### Two-week plan

**Week 1 (2026-09-11 to 2026-09-18) - close the list, full regression**
- Boboiboy: items 1-4 above.
- Yaya: full regression pass on both boxes - multi-camera, restart-under-load,
  the exact failure modes fixed this week, so none regress silently. Formal
  test cases, not spot checks; catalogue kept current in `docs/test-cases.md`.
- Ying: contrast/theming pass over every dashboard page in both light and
  dark mode; SDD write-up; resolve item 5 with Tok Aba.
- Gopal: keep the running note current as items close; flag conflicts
  between roles early.

**Week 2 (2026-09-18 to 2026-09-25) - freeze, UAT, docs**
- Code freeze mid-week (2026-09-21); only P0 fixes admitted after that.
- Tok Aba: UAT pass, once Yaya's regression pass is done and reported clean.
- Amato: beta release notes, known-issues doc, changelog rollup, this plan
  kept current.

### Risks specific to this milestone

| Risk | Why it matters | What reduces it |
| --- | --- | --- |
| A fix verified only in the sandbox suite, not on real hardware | Several of this week's bugs (chip contention, camera timing, CPU load) only show on the physical Pi + Hailo boards | Every fix this milestone gets a live-hardware check on .63 and/or .234 before it counts as done |
| Beta ships with an item still open because "it's not really a bug" | Item 5 above is exactly that shape - a canvas that looks broken but might be intentional | Decided explicitly with Tok Aba, recorded here, not left ambiguous |
| Two-week target slips because regression finds more than expected | Yaya's Week 1 pass is new-ground testing, not a re-run of known cases | Freeze date is fixed; if regression is still finding things at freeze, that is raised to Tok Aba as a scope call, not silently absorbed |

## Milestone: over-the-air updates (mostly complete)

The current milestone. Ordered so that each phase is independently useful and
independently testable on a real board, and so that the phase that can brick a
box comes after everything that cannot.

| Phase | Deliverable | State |
| --- | --- | --- |
| 1 | Platform version: `VERSION`, `core/version.py`, version in `/api/health` and the UI | done |
| 2 | Release tooling (`scripts/dev/publish_release.py`), `config/update.toml`, private `irivai-release` repository, first hand-published release | tooling done; repository and first release pending |
| 3 | Device check path: service, admin routes, background checker, Platform Update page | done |
| 4 | Applier, boot-time recovery, appliance release layout, units, sudoers | written; hardware validation pending |
| 5 | CI release workflow on tag push | written; needs Actions secrets |
| 6 | `tests/smoke_update.py` | done, part of the full suite (202/203 passing, 1 skipped, as of 2026-09-11) |
| 7 | Documentation set + weekly changelog | in progress |

**Remaining, in order:**

1. Create the private `irivai-release` repository and the two tokens - a
   read-only device token (Contents, that repository only) and a read/write
   publish token for CI.
2. Generate the OTA signing key (`publish_release.py keygen`); paste the
   public half into `config/update.toml` and `core/services/update.py`; store
   the private half as the `OTA_SIGNING_KEY` Actions secret.
3. Hand-publish v2.1.0, then confirm the check path finds it from a board.
4. Re-run `scripts/install.sh` on a test board to convert it to the release
   layout, then apply a trivial v2.1.1 end to end.
5. Run the fault drills in `docs/test-plan.md` before any customer box is
   pointed at the channel.

## Next milestones (post-beta)

**Code protection (compile to `.so`).** Today the release bundle ships plain `.py`
source - `.pyc` is actively excluded from the bundle, not shipped as a lighter
protection layer, and the only real barrier to an appliance owner editing the code
is SSH/shell lockout, which `install.sh` warns about but does not enforce.
Raised 2026-09-11: compile the detection app and core modules with Cython to real
`.so` binaries for the aarch64 (CM5) target - genuine protection, not just
bytecode-only distribution. Non-trivial: OTA's `repair_store_apps` /
`_APP_CODE_ITEMS` sync plain `.py` files today and would need reworking for
compiled artifacts, and the full 203-file suite needs to pass again against
compiled modules (particularly `functions.py`'s AST-whitelist evaluator, which
does source-level things worth checking carefully once compiled). **Decision
2026-09-11: post-beta, not a beta blocker** - Boboiboy's Week 1 is already full
(4 open items) and this touches the same OTA sync path he'd also be hardening.
Owner: Boboiboy (build), Pipi (sign-off on whether `.so` is sufficient protection
for the actual threat - a curious customer vs. someone determined to
reverse-engineer it need different answers).

## Next milestones

**Fleet visibility.** OTA gives a box the ability to update itself; it does
not give anyone a view of which boxes are on which version. The natural next
step is reporting version and update state upward through the existing device
Contract (`docs/contract-v1.md`), where `platform_version` is already a field
the hub displays but nothing on a box produces.

**Staged rollout.** Once more than a handful of boxes exist, publishing to all
of them at once stops being acceptable. A release channel field (stable,
beta) in `config/update.toml` is the smallest version of this.

**Update token lifecycle.** Fine-grained tokens expire within a year. The
product now surfaces expiry distinctly in the UI; what it does not have is a
rotation path that does not involve pasting a new one by hand.

## Resources and cadence

**Updated 2026-09-11.** Five roles now, working in single-item flow with
same-day hardware feedback: **Boboiboy** (Senior Software Engineer),
**Ying** (Senior Graphic Engineer), **Yaya** (Senior Software Tester),
**Amato** (Senior Project Manager, this document), **Gopal** (Senior
Assistant Engineer). **Tok Aba** owns UAT and final acceptance. Hardware:
two boards, `192.168.1.234` (Hailo-8) and `192.168.1.63` (Hailo-10H), plus
customer boards in the field. No fixed release cadence outside the beta
target above - a release is cut when there is something worth shipping.

## OTA-specific risks

| Risk | Why it matters | What reduces it |
| --- | --- | --- |
| An update that does not come up in the field | The box has no shell and no one on site | Two-stage health gate, automatic rollback, boot-time repair, one previous release always kept |
| A dependency without an aarch64 wheel | The board would try to compile at install time, on a machine that is meant to be detecting | The release job fails at wheelhouse time rather than on the appliance |
| The OTA signing key leaking from CI | Signed updates are the trust root of the whole box | A key used for nothing else; private repository; rotate by publishing a release with a new public key |
| Boxes that predate the release layout | They cannot flip a symlink they do not have | Detected and said plainly on the page; fixed by re-running the installer once |
| A defect log that stops being written | The only record of why a fix exists | It is part of the definition of done, and the weekly file makes a gap visible |
