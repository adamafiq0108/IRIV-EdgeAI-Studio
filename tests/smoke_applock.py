# The per-system app lock really excludes a second caller.
#
# WHAT WAS WRONG (review of 37367cb): the hub and a system's master both
# start and stop that system's app, with nothing between them. A master
# spawned a logic-mode app; the hub's Start detection landed inside the 0.8 s
# before that app's pid was recorded, found an unrecorded process of the
# package, "re-adopted" it -- and answered "detection started" over an app
# with no model and no camera. 5bb48e4 put every start and stop under
# core/services/app_lock.held(slug). Since 401b7b9 there is one app per
# system with AI detection a switch inside it, and the lock is held by
# dc.start / stop / stop_app / ensure_app and by the app supervisor's
# look-and-act (core/services/app_supervisor.py) -- still a hub (System
# Control, Power) and a master (Detection page, supervisor) per system.
#
# smoke_logicmode checks that the lock is TAKEN (the decorators are there)
# and that it is re-entrant. Nothing checked that it EXCLUDES anybody. This
# does, by behaviour:
#   1. two threads of one process holding one slug never overlap
#   2. two different slugs do not queue behind each other (a 10 s stop of one
#      system must not hold up another system's start)
#   3. re-entry in one thread does not release the lock for another thread
#   4. across PROCESSES (hub vs master): a child holding the lock makes the
#      parent wait until it lets go. POSIX only -- on Windows the module
#      degrades to a process-local lock by design, so that part is skipped
#      there (said so, not passed).
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import app_lock                             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. one slug, two threads: never inside together ---------------------------
inside = []
overlap = []


def worker(tag):
    for _ in range(5):
        with app_lock.held("sys_a"):
            inside.append(tag)
            if len(inside) > 1:
                overlap.append(list(inside))
            time.sleep(0.02)
            inside.remove(tag)


threads = [threading.Thread(target=worker, args=(t,)) for t in ("t1", "t2", "t3")]
for t in threads:
    t.start()
for t in threads:
    t.join(10)
check("one system: two callers are never inside the lock together",
      overlap == [], overlap)

# -- 2. different slugs do not queue --------------------------------------------
release = threading.Event()
got_b = []


def hold_a():
    with app_lock.held("sys_a"):
        release.wait(5)


ta = threading.Thread(target=hold_a)
ta.start()
time.sleep(0.1)
t0 = time.time()
with app_lock.held("sys_b"):
    got_b.append(time.time() - t0)
release.set()
ta.join(5)
check("another system's lock is not held up by this one's",
      got_b and got_b[0] < 1.0, got_b)

# -- 3. re-entry in one thread keeps the lock for the others --------------------
events = []
entered = threading.Event()
leave = threading.Event()


def nested():
    with app_lock.held("sys_c"):
        with app_lock.held("sys_c"):
            pass                      # inner exit must NOT release the outer
        entered.set()
        leave.wait(5)
        events.append("outer-exit")


def other():
    entered.wait(5)
    with app_lock.held("sys_c"):
        events.append("other-in")


tn, to = threading.Thread(target=nested), threading.Thread(target=other)
tn.start()
to.start()
time.sleep(0.3)
leave.set()
tn.join(5)
to.join(5)
check("re-entry: the inner exit does not let another thread in early",
      events == ["outer-exit", "other-in"], events)

# -- 4. across processes ----------------------------------------------------------
try:
    import fcntl                                               # noqa: F401
    have_flock = True
except ImportError:
    have_flock = False
if not have_flock:
    print("skip cross-process exclusion: no fcntl on this OS (the lock is "
          "process-local here by design; run this on the board)")
else:
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, %r)\n"
         "from core.services import app_lock\n"
         "with app_lock.held('sys_x'):\n"
         "    print('held', flush=True); time.sleep(2.0)\n" % ROOT],
        stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    line = child.stdout.readline().strip()
    t0 = time.time()
    with app_lock.held("sys_x", timeout_s=10):
        waited = time.time() - t0
    child.wait(10)
    check("another PROCESS holding the lock makes this one wait for it",
          line == "held" and waited >= 1.5, (line, waited))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all app lock checks passed")
