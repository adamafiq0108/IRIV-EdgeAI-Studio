"""The GStreamer plugin cache is rebuilt in a child process, with a time limit.

Seen on .164 (2026-10-19): after a reboot the app sat for minutes in
publisher.available() -> Gst.init(), which forks gst-plugin-scanner when the
plugin cache is stale. The Hailo gRPC client already existed in the process, so
the scanner and the app waited on each other for ever, the app never opened its
port, and the page said "Detection is busy". publisher.warm_registry() asks a
child (gst-inspect-1.0 --exists) first, so the cache is rebuilt there, and
gives up after a time limit so a bad cache degrades to MJPEG instead of a
stuck app.

  1. no gst-inspect-1.0: None (carry on as before)
  2. every element found: True, one child call per element
  3. one element missing: False, and the rest are not asked
  4. a child that does not finish: False (the box serves MJPEG)
  5. available() returns False on that without importing gi

    python tests/smoke_gstwarm.py
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "publisher_under_test", os.path.join(ROOT, "systems", "demo", "app", "publisher.py"))
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class Done:
    def __init__(self, code):
        self.returncode = code


def run_with(which, runner):
    real_which, real_run = pub.shutil.which, pub.subprocess.run
    pub.shutil.which, pub.subprocess.run = which, runner
    try:
        return pub.warm_registry(timeout_s=5)
    finally:
        pub.shutil.which, pub.subprocess.run = real_which, real_run


# -- 1. no gst-inspect-1.0 ----------------------------------------------------
check("no gst-inspect-1.0: None, nothing run",
      run_with(lambda n: None, lambda *a, **k: 1 / 0) is None)

# -- 2. all there ---------------------------------------------------------------
calls = []


def all_ok(cmd, **kw):
    calls.append(cmd)
    return Done(0)


check("every element found: True",
      run_with(lambda n: "/usr/bin/" + n, all_ok) is True)
check("...one child call per element, by name, with --exists",
      [c[-1] for c in calls] == list(pub._ELEMENTS)
      and all(c[1] == "--exists" for c in calls), str(calls))

# -- 3. one missing ---------------------------------------------------------------
calls.clear()


def second_missing(cmd, **kw):
    calls.append(cmd)
    return Done(1 if cmd[-1] == pub._ELEMENTS[1] else 0)


check("an element is missing: False",
      run_with(lambda n: "/usr/bin/" + n, second_missing) is False)
check("...and the elements after it are not asked", len(calls) == 2, str(calls))


# -- 4. a child that never finishes ---------------------------------------------
def hangs(cmd, **kw):
    raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))


check("a child that does not finish: False (the app serves MJPEG)",
      run_with(lambda n: "/usr/bin/" + n, hangs) is False)

# -- 5. available() does not reach Gst.init on that ---------------------------------
real_which, real_warm = pub.shutil.which, pub.warm_registry
pub.shutil.which, pub.warm_registry = (lambda n: "/usr/bin/" + n), (lambda *a, **k: False)
sys.modules.pop("gi", None)
try:
    got = pub.available()
finally:
    pub.shutil.which, pub.warm_registry = real_which, real_warm
check("available() is False when the cache will not build, gi never imported",
      got is False and "gi" not in sys.modules)

# -- the app asks warm_registry before it forks anything -------------------------------
src = open(os.path.join(ROOT, "systems", "demo", "app", "publisher.py"), encoding="utf-8").read()
body = src.split("def available():", 1)[1].split("class Publisher", 1)[0]
check("available() warms the registry before it calls Gst.init",
      body.index("warm_registry()") < body.index("Gst.init(None)"))

print(f"\n{'%d FAILED' % bad if bad else 'all good'}")
sys.exit(1 if bad else 0)
