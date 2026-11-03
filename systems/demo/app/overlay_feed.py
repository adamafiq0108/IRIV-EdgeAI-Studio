# -*- coding: utf-8 -*-
"""
overlay_feed.py -- the live overlay as DATA, for a browser that draws it.

The live view used to be painted here: boxes, zones, links, bubbles, trails
and the HUD burned into every frame before it went to WebRTC or MJPEG. That
is a full-frame paint per published frame plus an H.264 re-encode of the
painted picture, per camera, on a 4-core CM5 that already saturates with
several cameras running AI (py-spy, tools/profile_cpu.py). With
[video] overlay = "client" the frames go out PLAIN and the browser draws the
same overlay on a canvas over the video, from the JSON built here.

Two halves:

  * build()  -- one camera's overlay snapshot (run.py `pipe.overlay`, the
    dict _paint draws) as a compact, NORMALISED message. Everything
    camera.annotate puts into the pixels is represented, with the colour it
    would have been painted in, so the browser never re-derives a rule.
  * Feed     -- who is listening, per camera. A message is built only while
    a browser is connected to that camera, at most `hz` times a second, and
    not serialised again when nothing in it changed. Each listener has a
    bounded queue that drops its OLDEST message: a slow browser sees the
    newest boxes late rather than a backlog of old ones.

The "server" mode does not come through here at all: nothing is built and
nothing is offered, so that path is exactly what it was.

The master reads the same [video] keys (core/services/overlay_view.py); the
app cannot import core/, so the parsing is duplicated and pinned by
tests/smoke_overlayclient.py.
"""

import collections
import json
import threading
import time

from app import camera as camera_mod

#: "server" burns the overlay into the frame (the default, the old picture);
#: "client" sends the plain frame plus this module's metadata.
MODES = ("server", "client")
DEFAULT_MODE = "server"

#: Messages per second per camera, at most. 10 is past what an eye follows on
#: a box outline and a tenth of the cost of matching a 100 fps camera.
DEFAULT_HZ = 10.0
MAX_HZ = 30.0

#: Queued messages per listener before the oldest is dropped. Per camera it
#: listens to, so one busy camera cannot push a quiet one's newest message out.
QUEUE_PER_CAMERA = 4

#: Listeners at once. A browser page holds ONE (it multiplexes its cameras),
#: so this is a dozen pages; past it the oldest is evicted, as the MJPEG
#: viewers are.
MAX_LISTENERS = 12

#: Format version, for a browser that outlives an app upgrade.
VERSION = 1


def settings(cfg):
    """{"mode", "hz", "delay_ms"} from a system's merged config. Anything
    unreadable is the default: a typo must cost the new mode, never the
    picture."""
    video = (cfg or {}).get("video") or {}
    if not isinstance(video, dict):
        video = {}
    mode = str(video.get("overlay") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        mode = DEFAULT_MODE
    try:
        hz = float(video.get("overlay_hz") or DEFAULT_HZ)
    except (TypeError, ValueError):
        hz = DEFAULT_HZ
    try:
        delay = int(float(video.get("overlay_delay_ms") or 0))
    except (TypeError, ValueError):
        delay = 0
    return {"mode": mode, "hz": min(MAX_HZ, max(1.0, hz)),
            "delay_ms": min(5000, max(0, delay))}


def client_mode(cfg):
    """True when the browser draws the overlay for this system."""
    return settings(cfg)["mode"] == "client"


def _hex(bgr):
    """cv2's (B, G, R) as the browser's "#rrggbb"."""
    b, g, r = (int(v) & 255 for v in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def _box_color(det):
    """The colour camera.annotate draws this box in: the alert's own colour,
    else red for a violation, else amber. Resolved HERE so the browser
    cannot disagree with a snapshot about which box tripped a rule."""
    rgb = camera_mod._det_rgb(det)
    if rgb:
        return "#%02x%02x%02x" % rgb
    return "#ff0000" if det.get("violation") else "#ffaa00"


def build(overlay, w, h):
    """One camera's overlay (the dict _paint draws) as a JSON-ready body,
    coordinates as fractions of a w x h frame (4 decimals: a tenth of a
    pixel on a 1920-wide frame). None when there is nothing to describe.

    Ordered as camera.annotate draws: trails, bubbles, zones, boxes, links,
    then the HUD on top. Optional members are left out when empty, so an
    idle camera costs a few dozen bytes.
    """
    if not overlay or not w or not h:
        return None
    fw, fh = float(w), float(h)

    def nx(v):
        return round(float(v) / fw, 4)

    def ny(v):
        return round(float(v) / fh, 4)

    def pt(p):
        return [nx(p[0]), ny(p[1])]

    body = {}
    trails = [[[pt(p) for p in pts], 1 if live else 0]
              for pts, live in overlay.get("trails") or [] if pts]
    if trails:
        body["trails"] = trails
    bubbles = [[nx(x1), ny(y1), nx(x2), ny(y2), 1 if hit else 0]
               for x1, y1, x2, y2, hit in overlay.get("bubbles") or []]
    if bubbles:
        body["bubbles"] = bubbles
    # Through the painter's own normaliser: it drops switched-off zones,
    # resolves each colour and falls back to the legacy rectangle exactly as
    # the picture does. While a zone is being dragged in the editor run.py
    # hands in no zones at all, and the message says so the same way.
    zones = []
    for pts, is_line, color, facing in camera_mod._zone_shapes(
            overlay.get("zone"), overlay.get("overlays") or []):
        z = {"kind": "line" if is_line else "poly",
             "pts": [pt(p) for p in pts], "color": _hex(color)}
        if is_line and facing in ("forward", "reverse"):
            z["facing"] = facing
        zones.append(z)
    if zones:
        body["zones"] = zones
    dets = []
    for d in overlay.get("detections") or []:
        box = d.get("box") if isinstance(d, dict) else None
        if not box or len(box) != 4:
            continue
        row = {"box": [nx(box[0]), ny(box[1]), nx(box[2]), ny(box[3])],
               "label": str(d.get("label", "")),
               "conf": round(float(d.get("confidence") or 0.0), 3),
               "color": _box_color(d)}
        if d.get("track_id") is not None:
            row["id"] = d.get("track_id")
        if d.get("violation"):
            row["viol"] = 1
        size = camera_mod.size_text(d)
        if size:
            row["size"] = size
        segs, ring, silhouette = camera_mod.measure_lines(d)
        measure = {}
        if segs:
            measure["segs"] = [[*pt(a), *pt(b), text] for a, b, text in segs]
        if ring is not None:
            (cx, cy), r, text = ring
            # A radius in WIDTH fractions: the video is scaled uniformly, so
            # one axis is enough to bring it back.
            measure["ring"] = [nx(cx), ny(cy), round(float(r) / fw, 4), text]
        if len(silhouette) >= 3:
            measure["sil"] = [pt(p) for p in silhouette]
        if measure:
            row["measure"] = measure
        dets.append(row)
    if dets:
        body["dets"] = dets
    links = [[nx(x1), ny(y1), nx(x2), ny(y2), 1 if within else 0,
              int(f"{float(dist):.0f}")]
             for x1, y1, x2, y2, within, dist in overlay.get("links") or []]
    if links:
        body["links"] = links
    hud = [[str(label), str(value)] for label, value in overlay.get("hud") or []]
    if hud:
        body["hud"] = hud
    return body


class Listener:
    """One connected browser: which cameras it wants and what is waiting to
    be written to it. Filled by Feed.offer on the detection thread, drained
    by the stream handler's thread, which sleeps on the condition rather than
    polling."""

    def __init__(self, cameras):
        #: Camera ids this listener receives. Empty means every camera.
        self.cameras = frozenset(cameras or ())
        self.queue = collections.deque(
            maxlen=QUEUE_PER_CAMERA * max(1, len(self.cameras) or 4))
        self.cond = threading.Condition()
        self.closed = False
        self.dropped = 0
        self.since = time.time()

    def wants(self, cam):
        return not self.cameras or cam in self.cameras

    def put(self, data):
        with self.cond:
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1           # deque drops the OLDEST on append
            self.queue.append(data)
            self.cond.notify()

    def get(self, timeout):
        """The next message, or None after `timeout` seconds of nothing (the
        caller then writes a keepalive) or once closed."""
        with self.cond:
            if not self.queue and not self.closed:
                self.cond.wait(timeout)
            return self.queue.popleft() if self.queue else None

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify()


class Feed:
    """Per-camera overlay messages, made only for cameras somebody watches."""

    def __init__(self, hz=DEFAULT_HZ, clock=time.time, resolver=None):
        self.hz = float(hz)
        #: False in "server" mode: the app's /overlay answers 404 and nothing
        #: is ever offered. Flipped by run.py on start and on a config reload.
        self.enabled = False
        #: camera id ("" = the bare path) -> the camera's real id, or None.
        self.resolver = resolver
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners = []
        #: cam -> (body, encoded message): the last thing sent, so an
        #: unchanged overlay is neither serialised nor sent again, and a
        #: browser joining mid-way gets the current picture at once.
        self._last = {}
        self._seq = {}
        self._sent_at = {}

    def resolve(self, cam_id):
        """The id a request's `camera` names: "" is the first camera."""
        if self.resolver is None:
            return cam_id or None
        return self.resolver(cam_id)

    def connections(self, cam=None):
        """How many browsers listen to `cam` (any camera when None). A count
        for this module's own gate, NOT a viewer gate for the video encode."""
        with self._lock:
            return sum(1 for li in self._listeners
                       if cam is None or li.wants(cam))

    def subscribe(self, cameras):
        """A new listener, primed with the last message of each camera it
        wants. At the cap the OLDEST listener is closed to make room."""
        li = Listener(cameras)
        with self._lock:
            if len(self._listeners) >= MAX_LISTENERS:
                old = min(self._listeners, key=lambda x: x.since)
                self._listeners.remove(old)
                old.close()
            self._listeners.append(li)
            primed = [data for cam, (_body, data) in self._last.items()
                      if li.wants(cam)]
        for data in primed:
            li.put(data)
        return li

    def unsubscribe(self, li):
        li.close()
        with self._lock:
            try:
                self._listeners.remove(li)
            except ValueError:
                pass
            # Nobody left for a camera: forget its last message, so the next
            # browser is not primed with a picture from minutes ago.
            for cam in list(self._last):
                if not any(x.wants(cam) for x in self._listeners):
                    self._last.pop(cam, None)

    def close_all(self):
        """Close every listener (the mode went back to "server")."""
        with self._lock:
            gone, self._listeners = self._listeners, []
            self._last.clear()
        for li in gone:
            li.close()

    def offer(self, cam, overlay, w, h, first=False, now=None):
        """The detection loop's newest overlay for `cam`. Returns True when a
        message was queued. Costs a lock and a count when nobody listens."""
        with self._lock:
            targets = [li for li in self._listeners if li.wants(cam)]
        if not targets:
            return False
        now = self._clock() if now is None else now
        if now - self._sent_at.get(cam, 0.0) < 1.0 / max(1.0, self.hz):
            return False
        body = build(overlay, w, h)
        if body is None:
            return False
        last = self._last.get(cam)
        if last is not None and last[0] == body:
            return False                    # nothing changed: nothing to send
        seq = self._seq.get(cam, 0) + 1
        self._seq[cam] = seq
        msg = {"v": VERSION, "cam": cam, "seq": seq, "ts": round(now, 3),
               "w": int(w), "h": int(h)}
        if first:
            # The system's first camera is what a bare (no camera) request
            # means, so a page asking that way can recognise its messages.
            msg["first"] = 1
        msg.update(body)
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self._sent_at[cam] = now
        with self._lock:
            self._last[cam] = (body, data)
        for li in targets:
            li.put(data)
        return True
