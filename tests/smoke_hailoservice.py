# -*- coding: utf-8 -*-
"""A multi_process app does not grab the accelerator exclusively the moment
the HailoRT service hiccups.

.164, 2026-10-11 (Yaya): hailort_service segfaulted 14 times since boot. An
app that started, or was dropped, inside one of those crashes logged
"multi_process asked for but the HailoRT service would not serve it ...
opening the accelerator exclusively" and took the chip -- every other system
on the box then failed with HAILO_OUT_OF_PHYSICAL_DEVICES until all of them
were restarted. The all-or-nothing rule (hailo._vdevice_params) broken by
the fallback itself. Pinned with a FAKE VDevice and a fake clock:

  1. the service failing N times then serving: no exclusive open, ever, and
     every failure logged; inside the call it is retried with short waits,
     a not-yet-active unit is waited on rather than asked
  2. still failing but younger than SERVICE_GRACE_S: the open RAISES (run.py's
     detector retry ladder tries again later), never exclusive
  3. never recovering: past the grace, today's exclusive fallback, logged
     with how long it waited
  4. another live service client on the box (its marker, its start time):
     never exclusive, however long; a stale or recycled pid does not count
  5. a service open marks this process; an exclusive fallback unmarks it
  6. the marker dir is per uid and must be this account's own private dir:
     another owner, a 0777 dir or a symlinked dir is not trusted (and then
     never exclusive); a foreign-owned or symlinked marker is not counted; a
     planted symlink at our own marker is removed, its target untouched

    py -3 tests/smoke_hailoservice.py
"""
import logging
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class _Params:
    multi_process_service = False


class _VDevice:
    """Refuses the next `fail_service` service opens with error 77."""
    fail_service = 0
    opens = []                        # "service" / "exclusive", in order

    def __init__(self, params):
        kind = "service" if params.multi_process_service else "exclusive"
        if kind == "service" and _VDevice.fail_service:
            _VDevice.fail_service -= 1
            _VDevice.opens.append("service-refused")
            raise RuntimeError("HAILO_RPC_FAILED(77)")
        _VDevice.opens.append(kind)
        self.service = params.multi_process_service

    @staticmethod
    def create_params():
        return _Params()


sys.modules["hailo_platform"] = types.SimpleNamespace(
    VDevice=_VDevice,
    HailoSchedulingAlgorithm=types.SimpleNamespace(ROUND_ROBIN=1))
from app import hailo                                          # noqa: E402

CLOCK = [1000.0]
SLEPT = []
ACTIVE = [True]
hailo._now = lambda: CLOCK[0]
hailo._sleep = lambda s: (SLEPT.append(s), CLOCK.__setitem__(0, CLOCK[0] + s))
hailo._service_active = lambda: ACTIVE[0]
MARKERS = tempfile.mkdtemp()
hailo._marker_dir = lambda: MARKERS
PEERS = []
hailo._other_service_clients = lambda: list(PEERS)


class _Log(logging.Handler):
    lines = []

    def emit(self, record):
        _Log.lines.append(record.getMessage())


hailo.logger.addHandler(_Log())
hailo.logger.setLevel(logging.INFO)
TRIES = 1 + len(hailo.SERVICE_RETRY_WAITS_S)


def reset(fail):
    _VDevice.fail_service = fail
    _VDevice.opens = []
    _Log.lines = []
    SLEPT.clear()
    hailo._service_failed_at = None


def open_mp():
    opened = {}
    try:
        return hailo._open_vdevice(None, True, opened), opened, None
    except RuntimeError as exc:
        return None, opened, exc


# -- 1. N failures then the service serves -----------------------------------
check("the grace is bounded and the in-call waits are short",
      0 < hailo.SERVICE_GRACE_S <= 60 and sum(hailo.SERVICE_RETRY_WAITS_S) <= 10,
      (hailo.SERVICE_GRACE_S, hailo.SERVICE_RETRY_WAITS_S))
reset(2)
vd, opened, err = open_mp()
check("two refusals then served, inside one call: a service client",
      vd is not None and vd.service and opened == {"service": True}
      and "exclusive" not in _VDevice.opens, (_VDevice.opens, err))
check("...each refusal logged with its try",
      sum("would not serve this client (try" in ln for ln in _Log.lines) == 2,
      _Log.lines)
check("...after short waits", SLEPT == list(hailo.SERVICE_RETRY_WAITS_S[:2]), SLEPT)

# A longer outage: refused for TRIES*3 opens, re-asked by the ladder.
reset(TRIES * 3)
outcomes = []
for step in (0, 8, 16):                    # the ladder's first retries
    CLOCK[0] += step
    vd, opened, err = open_mp()
    outcomes.append("raise" if err else ("service" if vd.service else "exclusive"))
vd, opened, err = open_mp()
outcomes.append("raise" if err else ("service" if vd.service else "exclusive"))
check("refused across several ladder retries, then served: never exclusive",
      outcomes == ["raise", "raise", "raise", "service"]
      and "exclusive" not in _VDevice.opens, (outcomes, _VDevice.opens))
reset(TRIES)
_vd, _o, err = open_mp()
check("a refusal inside the grace raises, naming the retry and the error",
      err is not None and "retrying it for up to" in str(err)
      and "(77)" in str(err), err)

# The unit is not back yet: waited on, not asked.
reset(1)
ACTIVE[0] = False
_vd, _o, err = open_mp()
ACTIVE[0] = True
check("a unit that is not active yet is waited on, not asked",
      _VDevice.opens == ["service-refused"] and err is not None
      and sum("is not active yet" in ln for ln in _Log.lines) == TRIES - 1,
      (_VDevice.opens, _Log.lines))

# -- 3. never recovers ---------------------------------------------------------
reset(10 ** 6)
CLOCK[0] = 5000.0
first = open_mp()
# One second short of the grace when this call's own waits are done.
CLOCK[0] += hailo.SERVICE_GRACE_S - 1 - sum(hailo.SERVICE_RETRY_WAITS_S)
before_grace = open_mp()
CLOCK[0] += 2
vd, opened, err = open_mp()
check("never recovering: no exclusive open inside the grace",
      first[2] is not None and before_grace[2] is not None
      and _VDevice.opens.count("exclusive") == 1
      and _VDevice.opens[-1] == "exclusive", _VDevice.opens)
check("...past it, today's exclusive fallback",
      vd is not None and not vd.service and opened == {"service": False}, err)
check("...logged with how long it waited and why",
      any("has not served this system for" in ln and "(77)" in ln
          and "opening the accelerator exclusively" in ln for ln in _Log.lines),
      _Log.lines)

# -- 4. another system is a service client ---------------------------------------
reset(10 ** 6)
PEERS[:] = [4242]
CLOCK[0] = 9000.0
open_mp()
CLOCK[0] += hailo.SERVICE_GRACE_S * 10
vd, opened, err = open_mp()
PEERS[:] = []
check("another live service client here: never exclusive, however long",
      vd is None and "exclusive" not in _VDevice.opens
      and "other systems here use it (pid 4242)" in str(err), err)

# The REAL marker reader (the one patched out above), on a faked /proc:
# 4242 is alive with the start time it wrote, 5151's pid was recycled (another
# start time), 6161 is gone.
import importlib.util                                           # noqa: E402
spec = importlib.util.spec_from_file_location(
    "hailo_fresh", os.path.join(ROOT, "systems", "demo", "app", "hailo.py"))
fresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fresh)
REAL_MARKER_DIR = fresh._marker_dir
STARTS = {"4242": "777", "5151": "999"}
fresh._marker_dir = lambda: MARKERS
fresh._start_time = lambda pid: STARTS.get(str(pid), "")
for pid, started in (("4242", "777"), ("5151", "123"), ("6161", "1")):
    with open(os.path.join(MARKERS, pid), "w", encoding="ascii") as f:
        f.write(started)
orig_isdir = os.path.isdir
os.path.isdir = lambda p: True if p == "/proc" else orig_isdir(p)
try:
    got = fresh._other_service_clients()
finally:
    os.path.isdir = orig_isdir
check("the marker reader: a live pid with its own start time counts; a "
      "recycled pid (other start) or a dead one does not", got == [4242], got)

# -- 5. marking ----------------------------------------------------------------
for name in os.listdir(MARKERS):
    os.remove(os.path.join(MARKERS, name))
reset(0)
open_mp()
mine = os.path.join(MARKERS, str(os.getpid()))
check("a service open marks this process", os.path.exists(mine), os.listdir(MARKERS))
reset(10 ** 6)
CLOCK[0] = 20000.0
open_mp()
CLOCK[0] += hailo.SERVICE_GRACE_S + 1
open_mp()
check("...the exclusive fallback unmarks it", not os.path.exists(mine),
      os.listdir(MARKERS))
reset(0)
opened = {}
hailo._open_vdevice(None, False, opened)
check("an exclusive open asked for as such is untouched: one open, no wait",
      _VDevice.opens == ["exclusive"] and not SLEPT and opened == {"service": False})

# -- 6. the marker dir is this account's own (Pipi S1-L1) ----------------------
# Another local uid could pre-create a shared /tmp name, plant symlinks the
# app followed, or plant markers. Owners and modes come through the _lstat /
# _fstat seams, so the checks run the same on Windows and on the box.
real_uid = getattr(os, "geteuid", lambda: None)()
check("the marker dir name is per uid",
      real_uid is None or REAL_MARKER_DIR().endswith("-%d" % real_uid),
      REAL_MARKER_DIR())
ME = 1000 if real_uid is None else real_uid
OWN = {}                     # path -> (uid, mode bits) overrides for _lstat
FOREIGN_FILES = [False]


def _fake(st, uid, mode):
    return types.SimpleNamespace(st_uid=uid, st_mode=mode)


def fake_lstat(path):
    st = os.lstat(path)
    uid, bits = OWN.get(path, (ME, 0o700))
    return _fake(st, uid, (st.st_mode & ~0o7777) | bits)


def fake_fstat(fd):
    st = os.fstat(fd)
    return _fake(st, ME + 1 if FOREIGN_FILES[0] else ME, st.st_mode)


fresh._uid = lambda: ME
fresh._lstat = fake_lstat
fresh._fstat = fake_fstat
os.path.isdir = lambda p: True if p == "/proc" else orig_isdir(p)
try:
    box = tempfile.mkdtemp()
    fresh._marker_dir = lambda: box
    with open(os.path.join(box, "4242"), "w", encoding="ascii") as f:
        f.write("777")
    check("an own private dir: a live marker counts",
          fresh._other_service_clients() == [4242])

    FOREIGN_FILES[0] = True
    check("a marker file owned by another uid is not counted",
          fresh._other_service_clients() == [], fresh._other_service_clients())
    FOREIGN_FILES[0] = False

    for label, override in (("owned by another uid", (ME + 1, 0o755)),
                            ("writable by others (0777)", (ME, 0o777)),
                            ("a symlink", None)):
        if override is None:
            def link_lstat(path, _real=fake_lstat):
                st = _real(path)
                if path == box:
                    return _fake(st, ME, 0o120777)
                return st
            fresh._lstat = link_lstat
        else:
            OWN[box] = override
        fresh._marker_warned.clear()
        mine = os.path.join(box, str(os.getpid()))
        fresh._mark_service_client(True)
        got = fresh._other_service_clients()
        check("a marker dir %s: not trusted (None), nothing written" % label,
              got is None and not os.path.exists(mine), (got, os.listdir(box)))
        OWN.pop(box, None)
        fresh._lstat = fake_lstat

    # ...and an untrusted dir keeps the app off the exclusive fallback.
    hailo._other_service_clients = lambda: None
    reset(10 ** 6)
    CLOCK[0] = 30000.0
    open_mp()
    CLOCK[0] += hailo.SERVICE_GRACE_S * 10
    vd, opened, err = open_mp()
    hailo._other_service_clients = lambda: list(PEERS)
    check("untrusted markers: never exclusive, the reason named",
          vd is None and "exclusive" not in _VDevice.opens
          and "cannot be trusted" in str(err), err)

    # Planted symlinks: the writer removes its own name rather than follow
    # it, and the reader does not count a linked marker. Real links only
    # (Windows needs a privilege for them).
    victim = os.path.join(tempfile.mkdtemp(), "users.local.toml")
    with open(victim, "w", encoding="ascii") as f:
        f.write("[users]\n")
    try:
        os.symlink(victim, os.path.join(box, str(os.getpid())))
        aimed = os.path.join(tempfile.mkdtemp(), "fake")
        with open(aimed, "w", encoding="ascii") as f:
            f.write("999")
        STARTS["5353"] = "999"
        os.symlink(aimed, os.path.join(box, "5353"))
        linked = True
    except (OSError, NotImplementedError):
        linked = False
    if linked:
        fresh._mark_service_client(True)
        with open(victim, encoding="ascii") as f:
            kept = f.read()
        mine = os.path.join(box, str(os.getpid()))
        check("a planted symlink at our marker: removed, the target untouched",
              kept == "[users]\n" and not os.path.islink(mine)
              and os.path.isfile(mine), kept)
        got = fresh._other_service_clients()
        check("a marker that is a symlink is not counted",
              got == [4242], got)
    else:
        print("skip planted-symlink cases (no symlink privilege here; they "
              "run on Linux)")
finally:
    os.path.isdir = orig_isdir

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
