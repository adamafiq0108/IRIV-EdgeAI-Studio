# Test Execution Report - 2026-09-11 (Yaya)

Living document for this beta cycle - one report per significant run, not
overwritten.

## Summary

| | |
| --- | --- |
| Run | `python tests/run_all.py` |
| Date | 2026-09-11 |
| Result | **202 passed, 1 skipped, 0 failed** |
| Duration | ~115s |
| Total test files | 203 (187 `smoke_*.py`, 13 `check_*`, 3 `test_*.mjs`) |

## The one skip

`smoke_nvmeprobe.py` - skipped: needs a POSIX box and a non-root user. Not a
gap in coverage; this test only makes sense run on the real hardware target
(a Pi with the NVMe mount), not the Windows dev box the suite otherwise runs
on. **Action for Week 1:** re-run this specific test on .63 or .234 directly
over SSH as part of the hardware regression pass, since it never gets a
chance to run in the normal `run_all.py` sweep.

## What this run covers, and what it does not

The suite is described in full in `docs/test-cases.md` / `Test Cases.docx`.
In short: these are behavioural and source-assertion tests run against a
temporary store on the dev machine. They catch logic regressions - a
compiler rule, an engine calculation, a route's auth gate - fast, on every
change.

**What a green `run_all.py` does NOT prove**, and why the Week 1 plan calls
for a separate hardware pass:

- Chip contention behaviour (Hailo-10H exclusive-access sharing) - only
  reproduces on real hardware with two systems actually running.
- Camera timing, RTSP jitter, CPU load under multiple live cameras.
- Anything the sandbox's mock detector/camera papers over by design.

Three of this week's most serious bugs (chip contention, camera-rate video
throughput, the zone-overlay CPU cost) were found by profiling the live
box, not by this suite - the suite was green the whole time they existed.
That is not a suite failure; those are hardware-shaped problems the suite
is not built to catch, which is exactly why the beta plan calls for a
separate live-hardware regression pass in Week 1, not just a green
`run_all.py`.

## Findings this pass

No new failures found in this run. Existing open items (tracked in
`docs/project-plan.md` "Open items blocking beta") are known issues, not
suite regressions:

1. Parking counter (`z`, box .63) reads 0 - not a test gap, a live
   configuration issue (line placement / stale model binding on the rule).
2. Two nodes disabled on the `counting_template_editor` workflow graph -
   by design or by accident, still needs Tok Aba's confirmation.

Neither is something `run_all.py` would catch: both are per-deployment
configuration state, not code paths the suite exercises.

## Week 1 QA plan (from here)

1. Full multi-camera / multi-system regression on .63 and .234 live,
   targeting the exact failure modes fixed this week (chip contention,
   line-counter accuracy, video/inference throughput, app pid/heartbeat
   reporting).
2. Run `smoke_nvmeprobe.py` directly on hardware (see above).
3. Formal test cases for each Week 1 fix Boboiboy closes, added to the
   suite as they land - not held until the end of the week.
4. Report back before Tok Aba's UAT pass is scheduled; UAT should not start
   on an unverified base.
