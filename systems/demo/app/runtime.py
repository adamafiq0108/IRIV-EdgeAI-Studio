# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
runtime.py -- the app's state file, and starting/stopping it as a process.

The adapter and the app are separate processes on purpose: the master must be
able to report "detection stopped" rather than the whole device vanishing. That
only works if liveness is a fact on disk, not a variable in memory.

state.json holds {pid, started_at, running, camera, detector, error, heartbeat}.
The loop rewrites it every cycle; `status()` treats a stale heartbeat as dead,
because a crashed process leaves its last state file behind looking healthy.
"""

import json
import os
import signal
import subprocess
import sys
import time

STATE_FILE = "state.json"
#: ONE app per system, with AI detection a switch inside it ([ai] on in
#: config.local.toml -- run.py ai_config). The master looks for this before
#: running the app with the AI off: a system still on app code from before the
#: switch would ignore [ai] and bring up a camera and the model.
UNIFIED = True
STALE_AFTER_S = 30          # ~3 missed heartbeats at the default cadence
#: The longest start() waits to see a new app either die or report in.
SURVIVE_S = 2.0
#: The "AI running" dot is green only on a heartbeat younger than this. The
#: loop writes one at least every FLUSH_EVERY_S, so a quiet 10 s means it is not turning frames
#: over -- a HEF load, a wedged camera read -- and "producing" is no longer
#: true, whatever the last heartbeat said.
AI_OK_FRESH_S = 10

LOG_FILE = "app.log"
#: Default cap on app.log. One previous file is kept (app.log.1), so the
#: on-disk bound is twice this. The loop overrides it from
#: [storage].max_log_mb via trim_log()'s max_bytes argument.
LOG_MAX_BYTES = 16 * 1024 * 1024


def trim_log(data_dir, max_bytes=LOG_MAX_BYTES):
    """Keep app.log under `max_bytes`: past the cap, the current contents move
    to app.log.1 (replacing the old one) and the file restarts empty.

    Works on a LIVE log: the writer holds the fd in O_APPEND mode (start()
    opens it "ab", and the loop's stdout/stderr are that fd), so after the
    copy the file is truncated in place and the next append lands at the new
    offset -- the rename trick would leave the writer appending to the
    renamed inode forever. Best-effort: on a platform that refuses to
    truncate an open file (Windows dev boxes), the log is simply left alone.
    """
    path = os.path.join(data_dir, LOG_FILE)
    try:
        if max_bytes <= 0 or os.path.getsize(path) <= max_bytes:
            return False
        with open(path, "rb") as src, \
                open(path + ".1", "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        os.truncate(path, 0)
        return True
    except OSError:
        return False


def _path(data_dir):
    return os.path.join(data_dir, STATE_FILE)


def write(data_dir, **fields):
    """Merge fields into the state file. Best-effort: a failed state write must
    never take down detection.

    Every write of `error` is STAMPED with the moment it happened. Without the
    stamp a failure reason is undated, and an undated reason cannot be told
    apart from a current one -- which is how a camera fault from a run that
    ended hours ago went on being rendered as a live alarm on a stopped
    system. start() clears both together, so the stamp always belongs to the
    error sitting beside it.
    """
    state = read(data_dir)
    state.update(fields)
    if "error" in fields:
        state["error_at"] = time.time() if fields["error"] else None
    state["heartbeat"] = time.time()
    try:
        tmp = _path(data_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, _path(data_dir))    # atomic; no half-written state
    except OSError:
        pass
    return state


#: The loop's per-frame state goes to disk at most this often (heartbeat()).
#: Well inside AI_OK_FRESH_S and STALE_AFTER_S, so neither can trip on a
#: healthy loop, and a fraction of the old once-per-frame rewrite: at 15 fps
#: that was 15 read-merge-rename cycles a second per system on an SD card.
FLUSH_EVERY_S = 1.0
#: Fields whose CHANGE is written at once, whatever the pacing: what the
#: pages turn into a red or green dot, and whether the app is there at all.
#: cam_watch too: the camera watchdog's episode, which the master turns into
#: one Inbox notice (app/camwatch.py, core/services/app_supervisor.py), and
#: ai_paused, detection stopped by the box for a missing camera.
URGENT_KEYS = ("running", "pid", "error", "ai_on", "ai_ok", "ai_error",
               "cam_watch", "ai_paused")
_MISSING = object()
#: data_dir -> {"flushed": when last written, "last": urgent values written}
_hb = {}


def heartbeat(data_dir, force=False, **fields):
    """The loop's per-frame state, written through write() at most every
    FLUSH_EVERY_S -- and at once when an URGENT_KEYS field changes, or when
    `force`. Returns True when it wrote.

    The fields are the WHOLE picture every frame (run.py passes them all),
    so the newest call simply wins; nothing between two writes is lost but
    a counter's intermediate values, which no reader keeps anyway. Direct
    write() calls (an error the moment it happens, start, stop) are not
    paced at all.
    """
    now = time.time()
    slot = _hb.setdefault(data_dir, {"flushed": 0.0, "last": {}})
    urgent = any(slot["last"].get(k, _MISSING) != fields[k]
                 for k in URGENT_KEYS if k in fields)
    if not (force or urgent or now - slot["flushed"] >= FLUSH_EVERY_S):
        return False
    write(data_dir, **fields)
    slot["flushed"] = now
    slot["last"] = {k: fields[k] for k in URGENT_KEYS if k in fields}
    return True


#: data_dir -> (when, last successfully parsed state). A transient read failure
#: (busy SD card, interrupted syscall) used to parse as {} -- and {} means "no
#: pid, no heartbeat", which status() honestly reports as STOPPED. One bad read
#: therefore flipped the System Control switch off and back on with nothing
#: actually changing. The file is rewritten every loop cycle, so a seconds-old
#: copy is a far better answer than an empty one; the heartbeat inside it still
#: goes stale on its own if the app genuinely died.
_read_cache = {}
_READ_GRACE_S = 10.0


def read(data_dir):
    try:
        with open(_path(data_dir), "r", encoding="utf-8") as f:
            state = json.load(f)
        _read_cache[data_dir] = (time.time(), state)
        return state
    except (OSError, ValueError):
        when, cached = _read_cache.get(data_dir, (0, {}))
        return cached if time.time() - when < _READ_GRACE_S else {}


def status(data_dir, pkg_dir=None):
    """{running, stalled, pid, uptime_s, ...}. `running` is the honest answer,
    not the last thing the app claimed before it died.

    RUNNING is decided by the PROCESS, not by the heartbeat. The loop writes
    state every frame, so a stale heartbeat used to mean "dead" -- but it also
    means "busy": loading a HEF takes tens of seconds, and reopening a dropped
    RTSP camera can block longer than that, both INSIDE the loop. During either
    one the app is alive, the stream is up and detection resumes by itself,
    while every page that asked confidently reported it stopped. Opening Zones
    while a camera was reconnecting was the way to see it.

    So a live pid is running, and a stale heartbeat is reported separately as
    `stalled` -- alive but not turning frames over. A caller that wants to say
    "busy" can; nothing has to pretend the app is gone.

    PID reuse is the obvious objection to trusting a pid: the recorded number
    could belong to something else entirely by now. _owns() answers it by
    checking the process really is this package's app.run.
    """
    state = read(data_dir)
    pid = state.get("pid")
    fresh = (time.time() - float(state.get("heartbeat") or 0)) < STALE_AFTER_S
    alive = bool(pid) and _alive(int(pid)) and _owns(int(pid or 0), pkg_dir)
    started = float(state.get("started_at") or 0)
    return {
        "running": alive,
        # "unified" (the one app, AI a switch inside it); "logic" / "detection"
        # only for an app still running code from before that. A state file
        # written before modes existed is detection.
        "mode": (state.get("mode") or "detection") if alive else None,
        # The AI switch as the app applied it, and whether detection is really
        # producing (a real detector, a camera giving frames) -- or why not.
        "ai_on": state.get("ai_on") if alive else None,
        "ai_ok": bool(alive and state.get("ai_ok")
                      and time.time() - float(state.get("heartbeat") or 0)
                      < AI_OK_FRESH_S),
        "ai_error": state.get("ai_error") if alive else None,
        # Detection stopped by the box, not the user: "camera_missing" while
        # no camera of the system is there (app/camwatch.py), else None.
        "ai_paused": state.get("ai_paused") if alive else None,
        # Alive, but the loop has not written a heartbeat within STALE_AFTER_S.
        "stalled": bool(alive and not fresh),
        "pid": pid if alive else None,
        "uptime_s": int(time.time() - started) if alive and started else 0,
        "camera": state.get("camera"),
        "detector": state.get("detector"),
        "model": state.get("model"),
        "fps": state.get("fps"),
        "infer_fps": state.get("infer_fps"),
        # LIVE faults only. A stopped app's state file still holds whatever
        # reason its last run died of, and reporting that as `error` made the
        # Detection page raise a red camera alarm for a system nobody had
        # started yet -- an alarm that then CLEARED when detection started,
        # backwards from what an alarm means. The reason itself is not thrown
        # away: an operator who stopped a system BECAUSE the camera failed
        # still wants to read why, so it moves to `last_error` (with the
        # moment it was written) and is shown as history rather than as a
        # fault. `cameras` has always been gated this way; `error` was not.
        "error": state.get("error") if alive else None,
        "last_error": None if alive else state.get("error"),
        "last_error_at": None if alive else state.get("error_at"),
        # Per-camera runtime (error, measured fps), for the multi-camera
        # Detection page: the top-level error is only the FIRST camera's.
        "cameras": (state.get("cameras") or None) if alive else None,
        "last_heartbeat": state.get("heartbeat"),
    }


def _owns(pid, pkg_dir=None):
    """Is this pid actually THIS package's detection app?

    Guards the one hole in trusting a pid: the number in state.json outlives
    the process, and the kernel reuses numbers, so a crashed app could leave a
    pid that now belongs to somebody's text editor. The app is always spawned
    as `-m app.run` with cwd set to the package (see start()), and both are
    readable from /proc, so the check is exact rather than a guess.

    True where it cannot be checked (no /proc -- Windows dev boxes, macOS):
    the answer there is the same one this function replaced, and refusing to
    report a running app because the platform has no /proc would be worse than
    the reuse it defends against.
    """
    if pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", "replace")
    except OSError:
        return True                     # no /proc: not checkable, not a lie
    if "app.run" not in cmdline:
        return False
    if not pkg_dir:
        return True                     # right kind of process, system unknown
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        # The cmdline READ, so /proc works and this process is visible -- but
        # its cwd is not, which is exactly what a DYING app looks like: the
        # kernel drops the cwd link as the process tears down, and reading it
        # gives EACCES/ESRCH. Claiming it as ours then adopted a corpse:
        # start() answered "already running (pid N) -- re-adopted", wrote that
        # dead pid into state.json, and the system never actually started. It
        # stayed that way, because every retry adopted the same ghost.
        #
        # Unverifiable is NOT ours. The cost of being wrong this way is one
        # extra spawn attempt; the cost the other way is a system that reports
        # itself running and is not.
        return False
    return os.path.realpath(cwd) == os.path.realpath(pkg_dir)


def _alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)                 # signal 0: existence check only
    except (OSError, ProcessLookupError):
        return False
    # A ZOMBIE is not alive. The app is a child of the master (or the hub),
    # and Python only reaps a finished child the next time its parent starts
    # another process -- so an app that quit on SIGTERM in 0.3 s sat as a
    # zombie, signal 0 still answered for it, and every Stop / Power off
    # waited out the full timeout (10 s) and then reported "killed". Reading
    # the state letter from /proc tells the two apart.
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b") ", 1)[1][:1] != b"Z"
    except FileNotFoundError:
        return False                    # gone between the two looks
    except (OSError, IndexError):
        return True                     # no /proc: signal 0 is all there is


# -- start/stop, driven by the control channel (app.start / app.stop) --------

def _stray_app_pids(pkg_dir):
    """Detection apps of THIS package that state.json does not know about.

    The pid file loses track when a doomed twin overwrites it and dies (its
    number then points at a corpse while the REAL app streams on), and a
    process the file has lost is a process stop() can never reach. /proc is
    the ground truth the file is not: same test _owns() applies, run the
    other way around.
    """
    if not os.path.isdir("/proc"):
        return []                       # Windows dev boxes: nothing to scan
    me = os.getpid()
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        if _owns(int(name), pkg_dir):
            out.append(int(name))
    return out


def start(pkg_dir, data_dir, mode="unified"):
    """Spawn the app detached. Returns (ok, message).

    There is one mode now, "unified": whether it detects is [ai] on in its
    config (run.py ai_config), which the caller writes first. `mode` stays a
    parameter so a caller from before the switch still gets an answer.

    Detached, and returning immediately, because this is called from an HTTP
    request the master is waiting on -- the same rule reboot-class actions
    follow (docs/contract-v1.md sec 11).
    """
    mode = "unified"
    now_st = status(data_dir, pkg_dir)
    if now_st["running"]:
        # An app from before the switch (logic or detection mode) is not
        # "already running": it cannot follow [ai]. The caller (dc.start)
        # stops it first; this refuses rather than adopt if it did not.
        if (now_st.get("mode") or "detection") != mode:
            return False, (f"the app is running in {now_st.get('mode')} mode "
                           f"- stop it before starting {mode}")
        return False, "already running"
    # The pid file said stopped -- but the file is bookkeeping, not truth. A
    # twin that died on its stream-port bind used to overwrite the pid with
    # its own and take the record down with it, leaving the REAL app running,
    # streaming, holding the camera and the Hailo -- invisible. Spawning
    # "again" then bred another doomed twin: Errno 98 on the port, a dead
    # HEF load under it, and the operator watching detection "end by itself".
    # /proc is the ground truth: adopt the survivor instead of racing it.
    strays = _stray_app_pids(pkg_dir)
    if strays:
        # Only a stray of the SAME mode is adopted. A process of the package
        # whose mode the record cannot vouch for (an old logic-mode app) is
        # stopped instead: adopting it is the race that answered "started"
        # over an app that could not follow the AI switch.
        recorded = read(data_dir).get("mode") or "detection"
        if recorded == mode:
            write(data_dir, pid=strays[0], running=True, error=None, mode=mode)
            return True, (f"already running (pid {strays[0]}) - the record had "
                          "lost track of it; re-adopted")
        for pid in strays:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + 10
        while time.time() < deadline and any(_alive(p) for p in strays):
            time.sleep(0.2)
    log_path = os.path.join(data_dir, LOG_FILE)
    try:
        # A restart is the safest rotation point: nobody holds the fd yet, so
        # an oversized log (one written before trim_log existed) rotates by
        # rename here rather than growing forever.
        if os.path.getsize(log_path) > LOG_MAX_BYTES:
            os.replace(log_path, log_path + ".1")
    except OSError:
        pass
    log = open(log_path, "ab", buffering=0)
    # WHERE THIS RUN BEGINS. app.log is append-only across runs, so a file on
    # a board that has been started and stopped for months holds every run's
    # output with nothing between them. Nothing could tell them apart: the
    # lines carry no timestamps, and the Logs page scrapes HailoRT's native
    # errors out of the tail and stamps them with the FILE's mtime -- so
    # starting a system whose last run died in September showed September's
    # "the AI detection service stopped" as a fault dated today.
    #
    # One line, written here because this is the only place that knows a run
    # is beginning. Tagged [APP] like everything else people grep for, and
    # matched by core/services/log_collect.NATIVE_START_RE, which stops
    # reading backwards when it reaches one.
    try:
        log.write(("[APP] ==== app started %s ====\n"
                   % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
    except OSError:
        pass                            # a log we cannot write is not a
                                        # reason to refuse to start detection
    # UNBUFFERED. The file handle above is, but the CHILD's own stdout is not:
    # Python block-buffers 8KB when stdout is a file, so a running app's most
    # recent lines sat in its buffer and reached app.log only when it exited.
    # An operator reading Logs live -- or anyone diagnosing a camera that just
    # stalled -- saw the previous run's output and nothing from the one they
    # were watching. The buffer is worth nothing here; the log is read live.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    env.pop("CYTRON_APP_MODE", None)        # the old logic-mode switch
    kwargs = {"stdout": log, "stderr": log, "cwd": pkg_dir, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", "app.run"], **kwargs)
    # Recorded AT ONCE, mode included: a second caller arriving during the
    # first 0.8 s must see this app -- in its mode -- not an unrecorded
    # process of the package it could mistake for its own. Nothing else was
    # running (checked above), so there is no live pid this can overwrite.
    # `detector` cleared: the app's own first write names it, which is how
    # the wait below knows the child got through its imports and config.
    write(data_dir, pid=proc.pid, started_at=time.time(), running=True,
          error=None, mode=mode, detector=None)
    # ...and a child that dies in its first moments (port taken, import
    # error) is un-recorded again, so the state never claims a dead app.
    # POLLED, not one fixed 0.8 s sleep: on a loaded box a child could take
    # longer than that just to import, and a fixed sleep then called a
    # doomed start "started". The wait ends as soon as the child dies, or
    # writes its first state (it is past the failures this guards), or
    # SURVIVE_S passes with it still alive.
    deadline = time.time() + SURVIVE_S
    while True:
        if proc.poll() is not None:
            write(data_dir, running=False, pid=None)
            return False, (f"the app exited immediately (code {proc.returncode}) "
                           "- see app.log")
        if time.time() >= deadline or read(data_dir).get("detector"):
            break
        time.sleep(0.05)
    return True, f"started pid {proc.pid}"


def stop(data_dir, timeout_s=10, pkg_dir=None):
    """Ask the loop to stop, then insist. Returns (ok, message).

    Stops every app of this package /proc can see, not only the recorded pid:
    a stray the pid file lost track of would otherwise survive every Stop the
    operator presses, holding the camera and the stream port forever."""
    state = status(data_dir, pkg_dir)
    strays = [p for p in (_stray_app_pids(pkg_dir) if pkg_dir else [])
              if p != state.get("pid")]
    for pid in strays:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def _end_strays():
        # SIGTERM is a request: an app stuck in a call to the AI chip does not
        # act on it, and a stray with no pid file was never insisted upon, so it
        # outlived its system (deleted systems left apps holding a Hailo slot).
        deadline_s = time.time() + timeout_s
        while time.time() < deadline_s and any(_alive(p) for p in strays):
            time.sleep(0.3)
        for p in strays:
            if _alive(p):
                try:
                    os.kill(p, getattr(signal, "SIGKILL", signal.SIGTERM))
                except OSError:
                    pass

    if not state["running"]:
        if strays:
            _end_strays()
            write(data_dir, running=False, pid=None)
            return True, f"stopped stray pid(s) {strays}"
        return False, "not running"
    pid = int(state["pid"])
    try:
        os.kill(pid, signal.SIGTERM if os.name != "nt" else signal.SIGTERM)
    except OSError as e:
        return False, f"could not signal pid {pid}: {e}"
    if strays:
        _end_strays()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _alive(pid):
            write(data_dir, running=False, pid=None)
            return True, f"stopped pid {pid}"
        time.sleep(0.3)
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass
    write(data_dir, running=False, pid=None)
    return True, f"killed pid {pid}"
