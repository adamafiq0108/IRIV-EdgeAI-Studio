# -*- coding: utf-8 -*-
"""
view_gate.py -- [video] "video saving": skip the view work nobody looks at.

Two optional switches, both OFF by default, both read live from the system's
config like the other [video] keys:

  on_demand = true       THIS module's business. For a camera on the legacy
                         view path (the app paints, encodes and publishes it),
                         stop doing that while nobody watches: no WebRTC
                         reader on the gateway, no MJPEG viewer here. A
                         snapshot or a clip paints its OWN evidence frame
                         when it is due (run.py _keep_shot/_shot_frame and
                         capture.due), so neither needs the live view awake.
                         Detection and the overlay metadata never stop.
  view_on_detect = true  The PAGE's business (frontend/src/lib/viewOnDetect.js):
  view_hold_s = 30       a live tile opens the video only while the AI sees
  view_classes = [..]    something (of the listed classes, when view_classes
                         is set; absent = any), and closes it view_hold_s
                         after the last such detection. Parsed here only so the app and the master
                         agree on one rule (core/services/video_saving.py,
                         pinned by tests/smoke_videosave.py).

The idea is the reference safety-box product's: build the pixel path only
when a viewer is attached, a snapshot is due or a clip is recording.

WHO IS WATCHING. A browser never talks to the app: WebRTC goes through the
gateway (MediaMTX), which lists every session as a reader of the camera's
path. One loopback request a second (v3/paths/list, no credentials needed
for a read on 127.0.0.1) covers every camera of the system, from ONE thread
per app (run.py _watch_viewers). The gateway is where the media flows and it
drops a dead session on its own timeouts, so a crashed browser cannot keep a
camera awake for ever the way a count of WHEP handshakes in the master would.
MJPEG viewers are counted from the app's own stream server.

FAIL SAFE. Anything that is not a clear "this path, zero readers" -- the API
down, an answer that does not parse, a path the gateway does not list (being
built or rebuilt), an exception in the watch -- counts as watched. A wrong
guess here costs CPU; the other wrong guess costs somebody a black picture.

Pure apart from readers(): Gate takes an injected clock, so
tests/smoke_ondemand.py drives it without sleeping.
"""

import json
import time
import urllib.request

#: How often the gateway is asked. This is the worst-case wait between a
#: browser's handshake and full-rate video, so it stays short; one small
#: local HTTP request a second is not a cost worth trading that for.
PRESENCE_S = 1.0
#: How long a camera keeps its full view work after the last viewer left. An
#: operator flipping between cameras, or a tile remounting, closes one session
#: and opens the next a moment later; pausing in between would make every
#: such switch a cold start. NOT the page's view_hold_s (that one is about
#: detections, and is the owner's 30 s).
IDLE_GRACE_S = 10.0
#: While paused the publish sends one plain frame every this many seconds.
#: Not zero: the master offers WebRTC only for a READY path (streaming.mode),
#: so a publish torn down while idle would answer the next viewer "use the
#: img" and that viewer would never become the reader that wakes it. It also
#: keeps the RTSP session well inside the gateway's 10 s readTimeout.
KEEPALIVE_S = 2.0

#: view_hold_s: the owner's default and the clamp the page applies too.
DEFAULT_HOLD_S = 30
MIN_HOLD_S = 2
MAX_HOLD_S = 120

#: The gateway's control API, loopback only (scripts/mediamtx/mediamtx.yml).
#: itemsPerPage well past any box's camera count, so one request is the list.
PATHS_URL = "http://127.0.0.1:9997/v3/paths/list?itemsPerPage=1000"


def _flag(value):
    """A switch from TOML: true only for a real true (or its usual spellings
    written by hand). Anything else is the default, OFF."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in ("true", "yes", "on", "1")


def hold_seconds(value):
    """view_hold_s clamped to MIN..MAX; unreadable is the default."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_HOLD_S
    return min(MAX_HOLD_S, max(MIN_HOLD_S, n))


def classes_list(value):
    """view_classes: a clean list of class names, or None (absent or not a
    list) meaning every class. Mirrors core/services/video_saving."""
    if not isinstance(value, (list, tuple)):
        return None
    out, seen = [], set()
    for item in value:
        name = str(item if item is not None else "").strip()[:120]
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= 500:
            break
    return out


def settings(cfg):
    """{"on_demand", "view_on_detect", "view_hold_s"} from a merged config,
    plus "view_classes" when somebody chose. Anything unreadable is the
    default: a typo must cost the saving, never the picture."""
    video = (cfg or {}).get("video") or {}
    if not isinstance(video, dict):
        video = {}
    out = {"on_demand": _flag(video.get("on_demand")),
           "view_on_detect": _flag(video.get("view_on_detect")),
           "view_hold_s": hold_seconds(video.get("view_hold_s"))}
    chosen = classes_list(video.get("view_classes"))
    if chosen is not None:
        out["view_classes"] = chosen
    return out


def readers(url=None, timeout=1.0):
    """{path name: number of readers} for every path on the gateway, or None
    when that is not known (API down, unparseable answer).

    A path whose entry has no readers list is left OUT rather than reported
    as zero, so a gateway version that words it differently reads as
    "unknown" -- and unknown means keep publishing (Gate below)."""
    try:
        with urllib.request.urlopen(url or PATHS_URL, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name, rd = item.get("name"), item.get("readers")
        if isinstance(name, str) and isinstance(rd, list):
            out[name] = len(rd)
    return out


class Gate:
    """Which cameras' view work is WANTED, from one look at who is watching.

    update() takes one row per camera -- (id, gateway readers or None for
    unknown, MJPEG viewers, exempt) -- and returns the cameras whose answer
    changed. A camera is wanted while it is exempt (passthrough, depth,
    measurement), while its reader count is unknown, while anybody reads it
    either way, and for IDLE_GRACE_S after the last of those. A camera the
    gate has never seen is wanted: the first look starts its grace, it does
    not end it.
    """

    def __init__(self, grace_s=None, clock=time.time):
        self.grace_s = float(IDLE_GRACE_S if grace_s is None else grace_s)
        self._clock = clock
        self._seen = {}             # camera id -> last time it was watched
        self._wanted = {}           # camera id -> the last answer given

    def wanted(self, cam_id):
        return self._wanted.get(cam_id, True)

    def update(self, rows, now=None):
        now = self._clock() if now is None else now
        changed = []
        ids = set()
        for cam_id, n_readers, n_mjpeg, exempt in rows:
            ids.add(cam_id)
            if (exempt or n_readers is None or n_readers > 0
                    or (n_mjpeg or 0) > 0):
                self._seen[cam_id] = now
            want = now - self._seen.setdefault(cam_id, now) < self.grace_s
            if want != self._wanted.get(cam_id, True):
                changed.append((cam_id, want))
            self._wanted[cam_id] = want
        for gone in set(self._wanted) - ids:      # a camera removed
            self._wanted.pop(gone, None)
            self._seen.pop(gone, None)
        return changed

    def reset(self):
        """Every camera wanted again (the switch went off, or the watch
        failed). Returns the cameras that were paused."""
        paused = [c for c, w in self._wanted.items() if not w]
        self._wanted.clear()
        self._seen.clear()
        return paused
