# -*- coding: utf-8 -*-
"""
run_all.py -- run every smoke test and report which failed.

These are end-to-end checks, not unit tests: each one drives a real service
against a temporary store and asserts on the behaviour an operator would see
(a total that stops counting flicker, a card that stays hidden, a route that
answers 401). They need no test framework -- `python tests/run_all.py` is the
whole contract, which is also what CI runs.

Each file is executed as its own process, so one test's monkeypatching or
module-level state cannot leak into the next. Those processes run in PARALLEL
(a pool of CPU count - 1 workers), each with its own TEMP/TMP/TMPDIR under one
per-run root, so two tests that both write "<temp>/cytron_cameras.json" (the
camera registry the app itself keeps in the temp dir) cannot see each other's
file -- and nothing a test leaves in the temp dir outlives the run.

    python tests/run_all.py                 # everything, in parallel
    python tests/run_all.py lineband zone   # only files whose name contains one
    python tests/run_all.py --serial        # one at a time, the old way
    python tests/run_all.py -j 4            # (or IRIV_TEST_JOBS=4) pool size
    python tests/run_all.py --keep-temp     # keep a failed test's temp dir

Output is always in sorted file order whatever order the tests finish in, and
the last line is "N/M passed[, K skipped] in Xs". Exit code 77 from a test is
a skip (the shell convention), anything else non-zero a failure.
"""

import concurrent.futures
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: Exit code a test uses to say "the precondition for this does not exist on
#: this machine" (the shell convention for skipped).
SKIP = 77

#: Tests that must not run alongside each other. They run one at a time, in
#: this order, on a single lane while the pool runs everything else. Keep this
#: list SHORT -- a test belongs here only when it shares something a per-test
#: temp dir cannot separate:
#:
#:   smoke_store.py, smoke_ext.py -- both use tests/store_sandbox/ (a fixed
#:     path inside the repo, git-ignored). smoke_store rmtree's and rebuilds
#:     it; smoke_ext runs INSIDE the system smoke_store installs there (and
#:     re-runs smoke_store itself when that install is missing). Concurrently,
#:     smoke_ext reads a tree smoke_store is in the middle of deleting.
SERIAL = ("smoke_store.py", "smoke_ext.py")


def _jobs(argv):
    """Pool size: -j N / --jobs N, else IRIV_TEST_JOBS, else CPU count - 1."""
    for i, a in enumerate(argv):
        if a in ("-j", "--jobs") and i + 1 < len(argv):
            return max(1, int(argv[i + 1]))
        if a.startswith("--jobs="):
            return max(1, int(a.split("=", 1)[1]))
        if a.startswith("-j") and a[2:].isdigit():
            return max(1, int(a[2:]))
    env = os.environ.get("IRIV_TEST_JOBS", "").strip()
    if env.isdigit():
        return max(1, int(env))
    return max(1, (os.cpu_count() or 2) - 1)


def _names(argv):
    """The name filters: every argument that is not an option or its value."""
    out, skip_next = [], False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a in ("-j", "--jobs"):
            skip_next = True
            continue
        if a.startswith("-"):
            continue
        out.append(a)
    return out


def run_one(path, tmp_root):
    """Run one test file in its own temp dir. Returns (rc, output, tmpdir,
    stdout); rc None means the interpreter for it is not installed."""
    name = os.path.basename(path)
    tmp = os.path.join(tmp_root, name.replace(".", "_"))
    os.makedirs(tmp, exist_ok=True)
    # CYTRON_STORE too: most tests set their own temp store, but a third use
    # setdefault() or none at all, and those would otherwise share whatever
    # store this shell points at (or the developer's real one) with every
    # other test running at the same moment.
    # PYTHONFAULTHANDLER: a test that crashes in C (a segfault at interpreter
    # exit) otherwise fails with no word at all.
    env = dict(os.environ, TEMP=tmp, TMP=tmp, TMPDIR=tmp,
               CYTRON_STORE=os.path.join(tmp, "store"), PYTHONFAULTHANDLER="1",
               # tests run on laptops and CI: the CM5/ATECC/Hailo check is off
               # for them (smoke_hwcheck.py switches it back on to test it)
               IRIV_HW_CHECK="off")
    runner = ["node"] if path.endswith(".mjs") else [sys.executable]
    try:
        proc = subprocess.run(runner + [path], capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=os.path.dirname(HERE), env=env)
    except FileNotFoundError:       # no node on this machine
        return None, f"{runner[0]} is not installed", tmp, ""
    return (proc.returncode, (proc.stdout or "") + (proc.stderr or ""), tmp,
            proc.stdout or "")


def main():
    argv = sys.argv[1:]
    serial = "--serial" in argv
    keep = "--keep-temp" in argv
    jobs = 1 if serial else _jobs(argv)
    only = _names(argv)
    # Node checks run beside the Python ones: check_jsx_refs catches a class
    # of bug the frontend BUILD cannot (a component used but never imported
    # renders as a blank page, and esbuild compiles it happily).
    # check_*.py too: a whole-tree check (undefined names) is not a smoke test
    # of one feature, but it has to RUN, and a file nobody globs is a file
    # nobody runs -- which is how it would come to pass every time.
    files = sorted(glob.glob(os.path.join(HERE, "smoke_*.py"))
                   + glob.glob(os.path.join(HERE, "check_*.py"))
                   + glob.glob(os.path.join(HERE, "check_*.mjs"))
                   + glob.glob(os.path.join(HERE, "test_*.mjs")))
    if only:
        files = [f for f in files
                 if any(name in os.path.basename(f) for name in only)]
    if not files:
        print("no tests matched")
        return 1

    tmp_root = tempfile.mkdtemp(prefix="iriv-tests-")
    started = time.time()
    results = {}                         # path -> (rc, output, tmpdir, stdout)
    done = threading.Condition()

    def record(path):
        try:
            res = run_one(path, tmp_root)
        except Exception as e:           # never leave the reporter waiting
            res = (1, f"run_all could not start it: {e!r}", "", "")
        with done:
            results[path] = res
            done.notify_all()

    def serial_lane(paths):
        for p in paths:
            record(p)

    lane = [f for f in files if os.path.basename(f) in SERIAL]
    lane.sort(key=lambda f: SERIAL.index(os.path.basename(f)))
    pooled = [f for f in files if f not in lane]

    if serial:
        workers = None
        threading.Thread(target=serial_lane, args=(files,), daemon=True).start()
    else:
        workers = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
        if lane:
            # One pool slot runs the serial list in order; the rest share
            # the parallel ones.
            workers.submit(serial_lane, lane)
        for p in pooled:
            workers.submit(record, p)

    failed, skipped, keep_dirs = [], [], []
    # Report in sorted order as soon as each next result is in, so a long
    # run still shows progress.
    for path in files:
        with done:
            while path not in results:
                done.wait()
            rc, output, tmp, stdout = results[path]
        name = os.path.basename(path)
        if rc is None:
            print(f"  skip {name} -- {output}")
            skipped.append(name)
        elif rc == 0:
            print(f"  ok   {name}")
        elif rc == SKIP:
            # A test whose PRECONDITION is absent on this machine (the store
            # tests need the owner's signing key, which a customer box and CI
            # correctly do not have). Reported, never silent -- a skip nobody
            # sees is how coverage quietly disappears.
            skipped.append(name)
            reason = stdout.strip().splitlines()
            print(f"  skip {name}" + (f" -- {reason[-1]}" if reason else ""))
        else:
            failed.append(name)
            keep_dirs.append(tmp)
            # The code, always: "all good" followed by FAIL is a process lost
            # at exit, and only the code (-11, -15, 120...) says how.
            print(f"  FAIL {name} (exit code {rc})")
            for line in output.strip().splitlines()[-12:]:
                print(f"       {line}")
        sys.stdout.flush()
    if workers is not None:
        workers.shutdown(wait=True)

    took = time.time() - started
    passed = len(files) - len(failed) - len(skipped)
    print(f"\n{passed}/{len(files)} passed"
          + (f", {len(skipped)} skipped" if skipped else "")
          + f" in {took:.0f}s")

    if failed and keep:
        # Keep only what a failure left behind; the passing tests' dirs go.
        for entry in os.listdir(tmp_root):
            full = os.path.join(tmp_root, entry)
            if full not in keep_dirs:
                shutil.rmtree(full, ignore_errors=True)
        print("temp dirs of failed tests kept under " + tmp_root)
    else:
        # ignore_errors: a test's stray child may still hold a file open on
        # Windows; a leftover in the system temp dir is not worth a failure.
        shutil.rmtree(tmp_root, ignore_errors=True)
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
