# -*- coding: utf-8 -*-
"""
camwatch.py -- restart AI detection by itself when a camera stops answering,
and stop it when the camera is not there at all.

Tok Aba (2026-10-06): "if the camera disconnected, then after 5s if camera
still not detected, restart the AI detection automatically."
Tok Aba (2026-10-07): "if there is no camera then stop the AI detection" --
and when the camera is plugged back in, detection starts again by itself.

The loop already has a CAMERA-level ladder (run.py _read_frame): ~2 s of
empty reads reopens the device three times, follows a USB camera to the
/dev/video index it re-enumerated onto, and settles to one try every 10 s.
That is the smallest restart there is, and by the time a camera has been
silent for 5 s it has already been tried. What is left is the bigger hammer
the operator reaches for by hand -- Stop detection, Start detection -- and
this module decides WHEN to swing it. run.py does the swinging, through the
very reload path the AI switch uses, so a watchdog restart releases and
reacquires the camera(s) and the accelerator exactly the way Stop and Start
do (no second way to open a Hailo VDevice).

CamWatch is pure bookkeeping, no I/O and no clock of its own, so the smoke
test drives it with a fake clock and the loop drives it with time.time():

  * a camera is LOST when detection is on, the camera is expected (not
    parked, not mid-open) and its last real frame is `after_s` old -- counted
    from when detection last (re)started, so a Start does not fire on a
    camera that has not had its first chance
  * the first restart comes at once; each one that does not bring the camera
    back waits BACKOFF_S before the next (5, 10, 30, then every 60 s), never
    a tight loop against a device that is not there
  * after the first FAILED restart the episode is marked for the admins'
    Inbox (the master posts it, core/services/app_supervisor.py), and its
    recovery is marked the same way: one notice each, not one per attempt
  * the user's switch always wins: detection off ends the episode silently

NOT FOUND is not the same fault as SILENT. A camera that is on the box but
gives no frames (a stalled driver, a stream that froze) is what the restarts
above are for. A camera that is not on the box at all -- no USB video device,
an RTSP host that refuses or does not answer, a GigE camera Aravis does not
list -- is not fixed by any number of restarts, and each one spends the
accelerator for nothing. So once the first restart has been tried and every
camera of the system probes as absent (present() below, run off the loop by
Presence), the next attempt is a PAUSE instead: run.py applies the config
with the AI forced off, exactly as Stop does (camera and accelerator
released, detection-fed rules held, free rules running), while the user's
[ai] on stays ON -- the box stopped it, not the user. A cheap presence check
(a stat, a TCP connect, an Aravis listing; never the chip, never a model)
then runs every PRESENCE_S, and the moment a camera is back run.py applies
the config as written again (the Start half). The user's Stop while it waits
ends the pause for good; nothing turns detection back on after that, and a
Start while the camera is still missing waits again at once. A camera whose
presence cannot be told (a file, a gst: pipeline) never pauses.

Multi-camera: the FIRST restart is the whole detection, as asked -- if the
fault is the shared detector or its batch, only that clears it. A retry while
another camera of the system is still delivering restarts only the lost
camera(s): the full restart has already shown the rest is healthy, and doing
it again every minute would drop the good cameras' tracks (and, on a shared
chip, poke every sibling system) for a camera that is unplugged. With no
camera delivering, every retry is the full restart. A pause needs EVERY
camera of the system absent; while any one is on the box the missing ones
keep the per-camera reopen. When a paused system gets one camera back, the
ones still missing carry on as an episode that has already had its full
restart (the resume was it), so their next try reopens only them.
"""

import glob
import os
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

#: Seconds without a frame before a camera counts as lost ([app]
#: camera_lost_restart_s; 0 switches the watchdog off).
LOST_AFTER_S = 5.0
#: Wait after restart attempt n before the next; the last value repeats.
BACKOFF_S = (5.0, 10.0, 30.0, 60.0)
#: What the AI dot says while an episode lasts.
REASON = "camera lost, restarting detection"
#: The state.json `ai_paused` word while detection is stopped for a missing
#: camera: the box's reason, beside the user's untouched [ai] on.
PAUSED = "camera_missing"
#: How often a paused (or restarting) system looks for its camera(s).
PRESENCE_S = 3.0
#: One presence probe's own limit (a TCP connect, a listing).
PROBE_TIMEOUT_S = 1.5
#: The ports a network camera's URL implies when it names none.
_NET_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443,
              "rtmp": 1935, "rtmps": 443}


def paused_text(cameras):
    """The AI dot's reason while detection is stopped for a missing camera."""
    cams = ", ".join(str(c) for c in cameras) or "camera"
    return (f"camera not found ({cams}): detection stopped, it starts again "
            "by itself when the camera is plugged back in")


# -- presence: is the camera on the box at all? ------------------------------

def _usb_nodes():
    """{/dev/videoN: real path} of the video nodes that hang off USB. A host
    without sysfs keeps every node (it cannot tell, so it does not judge)."""
    out = {}
    for path in glob.glob("/dev/video[0-9]*"):
        sysdev = f"/sys/class/video4linux/{os.path.basename(path)}/device"
        if os.path.isdir("/sys/class/video4linux"):
            try:
                if "/usb" not in os.path.realpath(sysdev):
                    continue        # the Pi's ISP and codec nodes
            except OSError:
                continue
        out[path] = os.path.realpath(path)
    return out


def _usb_present(index):
    """A numeric source: is a USB camera there for it?

    /dev/cam-usb<n> (scripts/udev) pins the number to a physical socket, so
    with the rule installed the camera is there when its own link is, or
    when some USB video node is not claimed by any socket link (a camera
    on a socket the rule does not map, which the reconnect ladder follows).
    Without the rule the ladder follows the camera to whatever index it
    re-enumerated onto, so any USB video node counts. Not Linux: unknown."""
    if os.name == "nt" or not os.path.isdir("/dev"):
        return None
    link = f"/dev/cam-usb{index}"
    if os.path.islink(link) and os.path.exists(os.path.realpath(link)):
        return _openable(os.path.realpath(link))
    nodes = _usb_nodes()
    links = glob.glob("/dev/cam-usb*")
    if not links:
        return any(_openable(real) for real in nodes.values())
    claimed = {os.path.realpath(p) for p in links}
    return any(_openable(real) for real in nodes.values()
               if real not in claimed)


def _openable(node):
    """Present AND openable: a node that is there but refuses to open (a
    driver mid-reset, a permission) is not a camera detection can start
    on. A plain open, closed at once -- no format set, no stream started
    -- so the device is never held from the detector (or from another
    system sharing it) for longer than the call."""
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.close(fd)
    except OSError:
        pass
    return True


def _net_present(url, timeout):
    """A network camera: does its host accept a TCP connection on the port
    the URL names? Refused, unreachable, unresolvable and silent all read
    as not there; a scheme with no TCP port (udp://) is unknown."""
    try:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        port = parts.port or _NET_PORTS.get(scheme)
    except ValueError:
        return None
    if scheme not in _NET_PORTS or not host or not port:
        return None
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _gige_present(source, timeout):
    """A GigE camera: does Aravis list it (by name, or any camera for a bare
    "gige")? No arv-tool on the box: unknown, never paused for."""
    name = source.split(":", 1)[1].strip() if ":" in source else ""
    for tool in ("arv-tool-0.8", "arv-tool"):
        try:
            out = subprocess.run([tool], capture_output=True, text=True,
                                 timeout=max(timeout, 3.0)).stdout or ""
        except FileNotFoundError:
            continue
        except (OSError, subprocess.SubprocessError):
            return None
        listed = [ln.split("(", 1)[0].strip() for ln in out.splitlines()
                  if "(" in ln and ln.split("(", 1)[0].strip()]
        return (name in listed) if name else bool(listed)
    return None


def present(source, timeout=PROBE_TIMEOUT_S):
    """Is the camera behind `source` on the box? True / False, or None when
    this source cannot be told (a file, a gst: pipeline, synthetic, a host
    that is not Linux) -- None is never a reason to stop detection."""
    s = str(source if source is not None else "").strip()
    if (not s or s == "synthetic" or s.startswith(("file:", "gst:"))
            or s.startswith("libcamera")):
        return None
    if s.isdigit():
        return _usb_present(s)
    if s == "gige" or s.startswith("gige:"):
        return _gige_present(s, timeout)
    if "://" in s:
        return _net_present(s, timeout)
    if os.path.isabs(s):
        # A device path (/dev/v4l/by-id/..., /dev/videoN): the node goes
        # with the camera.
        return os.path.exists(s)
    return None


def probe_all(sources, timeout=PROBE_TIMEOUT_S):
    """{camera id: present()} for {camera id: source}, the probes side by
    side; one that has not answered within the limit is unknown (None)."""
    got = {}
    threads = []
    for cid, src in dict(sources).items():
        def run(cid=cid, src=src):
            try:
                got[cid] = present(src, timeout)
            except Exception:                                   # noqa: BLE001
                got[cid] = None
        t = threading.Thread(target=run, name=f"camwatch-probe-{cid}",
                             daemon=True)
        t.start()
        threads.append(t)
    end = time.monotonic() + timeout + 3.5
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    return {cid: got.get(cid) for cid in sources}


def all_missing(sources, timeout=PROBE_TIMEOUT_S):
    """The camera ids, sorted, when EVERY one of `sources` probes as absent;
    [] when any is there, cannot be told, or there are none. What the
    master's Start asks to word its answer, and what the app asks on a
    Start after the user stopped a wait, before it spends the accelerator."""
    if not sources:
        return []
    got = probe_all(sources, timeout)
    if got and all(v is False for v in got.values()):
        return sorted(got)
    return []


class Presence:
    """The presence probes, off the loop: poll() starts a sweep at most every
    `every_s` on a daemon thread and answers the newest finished one -- a
    TCP connect to a dead host must not hold the free rules for a second."""

    def __init__(self, every_s=None):
        # Read at construction, not bound at import, so a test can shorten it.
        self.every_s = float(PRESENCE_S if every_s is None else every_s)
        self._lock = threading.Lock()
        self._busy = False
        self._started = None        # when the newest sweep began
        self._seen = None           # (began, sources, {id: present})

    def poll(self, now, sources, since=None):
        """The newest answer for exactly `sources`, from a sweep begun at or
        after `since` -- else {} -- and a new sweep if one is due."""
        sources = dict(sources)
        with self._lock:
            due = not self._busy and (self._started is None
                                      or now - self._started >= self.every_s
                                      or now < self._started)
            if due:
                self._busy, self._started = True, now
            seen = self._seen
        if due:
            threading.Thread(target=self._sweep, args=(now, sources),
                             name="camwatch-presence", daemon=True).start()
        if (seen is None or seen[1] != sources
                or (since is not None and seen[0] < since)):
            return {}
        return dict(seen[2])

    def _sweep(self, began, sources):
        try:
            got = probe_all(sources)
        finally:
            with self._lock:
                self._busy = False
        with self._lock:
            self._seen = (began, sources, got)


# -- the watchdog's bookkeeping ----------------------------------------------

class CamWatch:
    """One system's camera watchdog. See the module docstring."""

    def __init__(self, after_s=LOST_AFTER_S, backoff=None):
        self.after_s = float(after_s)
        # Read at construction, not bound at import, so a test can shorten it.
        self.backoff = (tuple(float(b) for b in (backoff or BACKOFF_S))
                        or (60.0,))
        self.armed_at = None        # detection last (re)started; None = off
        self.episode = None         # the live episode, or None
        self.last = None            # the newest episode, kept for the master
        # The newest stop for a missing camera, live ("paused") or finished
        # ("resumed" / "stopped"), kept for the master like `last`.
        self.hold = None

    def configure(self, after_s):
        """[app] camera_lost_restart_s, read on every config reload."""
        try:
            self.after_s = max(0.0, float(after_s))
        except (TypeError, ValueError):
            self.after_s = LOST_AFTER_S

    def arm(self, now):
        """Detection was switched on, or restarted: the lost clock counts
        from here, not from a frame the previous run saw."""
        self.armed_at = now

    def disarm(self, now=None):
        """Detection is off (the user's Stop, or the AI switch off). Any
        episode ends without a recovery notice -- nobody is waiting for a
        camera detection is no longer using -- and so does a stop for a
        missing camera: the user's Stop wins, nothing starts it again."""
        self.armed_at = None
        if self.episode is not None:
            self.last = dict(self.episode, state="stopped", ended=now)
            self.episode = None
        if self.paused:
            self.hold = dict(self.hold, state="stopped", ended=now)

    @property
    def restarting(self):
        return self.episode is not None

    @property
    def paused(self):
        """Detection is stopped because no camera of the system is there."""
        return self.hold is not None and self.hold.get("state") == "paused"

    def lost(self, now, expected, frames, opening=()):
        """The expected cameras whose last frame is `after_s` old."""
        if self.armed_at is None or self.after_s <= 0:
            return []
        out = []
        for cid in expected:
            if cid in opening:
                continue                # still connecting: not judged yet
            last = max(float(frames.get(cid) or 0.0), self.armed_at)
            if now - last >= self.after_s:
                out.append(cid)
        return out

    def _fresh(self, now, cid, frames, since):
        got = float(frames.get(cid) or 0.0)
        return got >= since and now - got < max(self.after_s, 1.0)

    def step(self, now, user_on, expected, frames, opening=(), present=None):
        """One look. Returns None, ("recovered", episode), ("pause", hold)
        or ("restart", scope, camera ids) with scope "full" | "cameras".
        `present` is {camera id: True/False/None} from Presence: a pause
        needs a restart already tried and every expected camera False."""
        if not user_on or self.after_s <= 0:
            self.disarm(now)
            return None
        if self.armed_at is None:
            self.armed_at = now
        expected = list(expected)
        lost = self.lost(now, expected, frames, opening)
        ep = self.episode
        if ep is None:
            if not lost:
                return None
            ep = self.episode = {"since": now, "cameras": sorted(lost),
                                 "attempts": 0, "next_at": now, "at": now,
                                 "failed": False, "state": "restarting"}
        else:
            # A camera that left the system (deleted, switched off) is no
            # longer waited for.
            ep["cameras"] = sorted(c for c in set(ep["cameras"]) | set(lost)
                                   if c in expected)
            if not ep["cameras"]:
                # Every camera it waited for was removed: nothing came BACK,
                # so no recovery notice either.
                self.last = dict(ep, state="stopped", ended=now)
                self.episode = None
                return None
            if ep["attempts"] and all(self._fresh(now, c, frames, ep["at"])
                                      for c in ep["cameras"]):
                done = dict(ep, state="recovered", ended=now)
                self.last = done
                self.episode = None
                return ("recovered", done)
        if now < ep["next_at"] or any(c in opening for c in ep["cameras"]):
            # Waiting out the backoff -- or a reopen is still in flight, and
            # closing a camera mid-connect leaves its grab thread holding
            # the device.
            return None
        if (ep["attempts"] and present and expected
                and all(present.get(c) is False for c in expected)):
            # A restart has been tried and not one camera of the system is
            # on the box: stop, do not spend the chip on another restart.
            return ("pause", self.pause(now, expected))
        ep["attempts"] += 1
        if ep["attempts"] >= 2:
            ep["failed"] = True         # the first restart did not do it
        ep["at"] = now
        ep["next_at"] = now + self.backoff[min(ep["attempts"],
                                               len(self.backoff)) - 1]
        # Every expected camera NOT in the episode is healthy: a lost one is
        # always in it (above), and one still connecting is not lost.
        healthy = [c for c in expected if c not in ep["cameras"]]
        scope = "full" if ep["attempts"] == 1 or not healthy else "cameras"
        self.last = ep
        return ("restart", scope, list(ep["cameras"]))

    def pause(self, now, cameras, by="watchdog"):
        """Detection stops for missing camera(s). `by` is "watchdog" (a
        restart found nothing) or "start" (the user's Start after they
        stopped a wait, the camera still not there). The restart episode,
        if any, ends here. A Start
        straight after the user stopped a pause, with the camera still
        missing, is the SAME absence: it keeps that pause's id, so the
        admins are not told twice."""
        ep = self.episode
        if ep is not None:
            self.last = dict(ep, state="paused", ended=now)
            self.episode = None
        self.armed_at = None
        prev = self.hold
        key = ep["since"] if ep is not None else now
        if (by == "start" and prev is not None
                and prev.get("state") == "stopped"):
            key = prev["id"]
        self.hold = {"id": key, "since": now, "cameras": sorted(cameras),
                     "state": "paused", "by": by, "back": []}
        return self.hold

    def resume(self, now, back, still=()):
        """Camera(s) `back` are on the box again: the pause is over and
        detection starts again (run.py's Start half). Cameras `still`
        missing carry on as an episode whose full restart this was."""
        if self.hold is None:
            return None
        self.hold = dict(self.hold, state="resumed", back=sorted(back),
                         ended=now)
        self.armed_at = now
        still = sorted(still)
        if still:
            self.episode = {"since": now, "cameras": still, "attempts": 1,
                            "at": now,
                            "next_at": now + self.after_s + self.backoff[0],
                            "failed": False, "state": "restarting"}
            self.last = self.episode
        return self.hold

    def report(self):
        """The state.json `cam_watch` field: the live episode, else the
        newest finished one (so the master's 30 s look still sees a quick
        recovery), else None -- with `pause`, the newest stop for a missing
        camera, beside it."""
        ep = self.episode or self.last
        out = None
        if ep is not None:
            out = {"episode": round(float(ep["since"]), 3),
                   "cameras": list(ep["cameras"]),
                   "attempts": int(ep["attempts"]),
                   "failed": bool(ep["failed"]),
                   "state": ep.get("state") or "restarting",
                   "next_s": (max(0.0, round(ep["next_at"] - ep["at"], 1))
                              if self.episode is not None else None)}
        h = self.hold
        if h is not None:
            out = dict(out or {}, pause={
                "id": round(float(h["id"]), 3),
                "cameras": list(h["cameras"]),
                "state": h["state"], "by": h.get("by") or "watchdog",
                "back": list(h.get("back") or [])})
        return out
