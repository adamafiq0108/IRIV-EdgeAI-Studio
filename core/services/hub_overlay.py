# -*- coding: utf-8 -*-
"""
hub_overlay.py -- the Live Wall's overlay: ONE event stream per hub page,
carrying the overlay messages of every client-mode system on the wall.

The wall's tiles pull video straight from each master, but a hub page
cannot read a master's GET /api/detector/overlay itself: mixed content on an
HTTPS hub, CORS and the tab's session header, and a browser's ~6
long-lived connections per origin. So the hub relays the overlay -- a few
KB of JSON a second -- and never the video.

Shape: the hub opens the master's /api/detector/overlay server-to-server,
one upstream per system, AS the operator (their cookie, renamed for the
plain-HTTP hop by masters.for_master, and the tab's X-Session when the page
sent one). Each upstream is a thread reading through preview_relay.Relay,
the same blocking-read-plus-shutdown close the master's own relays use; its
events are wrapped with the system's slug and put on ONE bounded queue that
the page's response drains. A master that is down is retried with a backoff
on its own thread and the others keep flowing; a master that answers
401/403/404 (signed out, not allowed, back on "server") is given up for
this stream.

Wire format to the browser, one SSE event per message:
    data: {"sys":"<slug>","m":{...the app's message...}}
    data: {"sys":"<slug>","reset":1}     -- that system's stream dropped
    : keepalive                          -- every KEEPALIVE_S of silence
The app's message already names its camera ("cam", plus "first": 1 for a
system's first camera), so a tile picks its feed by slug and camera.

Framework-free (docs/conventions.md sec 2): the route owns the HTTP.
"""

import collections
import http.client
import json
import logging
import re
import threading
import urllib.parse

from core.services import masters as masters_service
from core.services import overlay_view
from core.services.preview_relay import Relay

logger = logging.getLogger(__name__)

#: A system name as the registry and the frontend (lib/base.js) spell it.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: Systems per stream: the wall has four tiles.
MAX_SLUGS = 4
#: Camera ids per stream, all systems together.
MAX_CAMERAS = 16
#: Open streams on this hub at once. A page holds ONE; past this the oldest
#: is closed (its page reconnects with a backoff), as the app evicts its
#: oldest overlay listener -- each stream holds a thread per system.
MAX_STREAMS = 8
#: Queued messages per camera before the oldest is dropped: a slow browser
#: sees the newest boxes late, never a backlog of old ones.
QUEUE_PER_CAMERA = 4
KEEPALIVE_S = 10.0
#: The app writes a keepalive every 10 s; this long a silence is a dead link.
READ_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 3
#: How long the route waits for every upstream's first answer.
FIRST_WAIT_S = 4.0
#: Upstream answers that end that system's part of the stream for good.
FINAL = (401, 403, 404)
#: An event bigger than this is not an overlay message; the tail is dropped.
MAX_EVENT_BYTES = 1 << 20

_LIVE = []
_LIVE_LOCK = threading.Lock()


def parse_wants(slugs, cameras):
    """{slug: [camera ids]} from `slugs=a,b` and repeated `camera=<slug>:<id>`
    ("<slug>:" = that system's first camera; a system named without one gets
    its first camera). Raises ValueError with a sentence for a bad request."""
    names = []
    for slug in str(slugs or "").split(","):
        slug = slug.strip()
        if not slug:
            continue
        if not SLUG_RE.match(slug):
            raise ValueError("not a system name")
        if slug not in names:
            names.append(slug)
    if not names:
        raise ValueError("name at least one system")
    if len(names) > MAX_SLUGS:
        raise ValueError(f"at most {MAX_SLUGS} systems per stream")
    wants = {slug: [] for slug in names}
    for item in cameras or []:
        slug, sep, cam = str(item or "").partition(":")
        if not sep or slug not in wants:
            raise ValueError("a camera names a system the stream did not")
        cam = cam.strip()
        if cam not in wants[slug]:
            wants[slug].append(cam)
    if sum(len(c) for c in wants.values()) > MAX_CAMERAS:
        raise ValueError(f"at most {MAX_CAMERAS} cameras per stream")
    return {slug: cams or [""] for slug, cams in wants.items()}


def envelope(slug, msg=None, reset=False):
    """One SSE event for the browser, tagged with its system."""
    body = {"sys": slug, "reset": 1} if reset else {"sys": slug, "m": msg}
    return b"data: " + json.dumps(body, separators=(",", ":")).encode() + b"\n\n"


def sse_data(buf):
    """Complete events' data out of `buf` (bytes): ([data, ...], rest).
    Comments (": keepalive") and other fields are dropped."""
    out = []
    buf = buf.replace(b"\r\n", b"\n")
    while b"\n\n" in buf:
        block, buf = buf.split(b"\n\n", 1)
        lines = [ln[5:].lstrip(b" ") for ln in block.split(b"\n")
                 if ln.startswith(b"data:")]
        if lines:
            out.append(b"\n".join(lines))
    return out, buf


class Upstream:
    """One master's /api/detector/overlay, read on its own thread."""

    def __init__(self, mux, slug, url, cameras, headers):
        parts = urllib.parse.urlsplit(str(url or ""))
        self.mux, self.slug, self.headers = mux, slug, headers
        # Masters are plain HTTP on this box; anything else is not one.
        self.host = parts.hostname if parts.scheme == "http" else None
        self.port = parts.port or 80
        self.path = "/api/detector" + overlay_view.upstream_path(cameras)
        self.status = None              # the first answer: 200, 4xx/5xx, 0 = down
        self.first = threading.Event()
        self.final = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._relay = None
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"hub-overlay-{slug}")

    def _answer(self, status):
        if self.status is None:
            self.status = status
        self.first.set()

    def _open(self):
        """(status, Relay or None). Never raises."""
        if not self.host:
            return 0, None
        conn = http.client.HTTPConnection(self.host, self.port,
                                          timeout=CONNECT_TIMEOUT_S)
        try:
            conn.request("GET", self.path, headers=self.headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            conn.close()
            return 0, None
        if resp.status != 200:
            conn.close()
            return resp.status, None
        return 200, Relay(conn, resp, read_timeout=READ_TIMEOUT_S)

    def _pump(self, relay):
        buf = b""
        for chunk in relay.chunks():
            datas, buf = sse_data(buf + chunk)
            if len(buf) > MAX_EVENT_BYTES:
                buf = b""
            for data in datas:
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self.mux.put(envelope(self.slug, msg))

    def _run(self):
        failures = 0
        while not self._stop.is_set():
            status, relay = self._open()
            if relay is not None:
                with self._lock:
                    if self._stop.is_set():
                        relay.close()
                        break
                    self._relay = relay
                self._answer(200)
                failures = 0
                # Until it ends or stop(). Whatever one attempt raises costs
                # that attempt only: an exception that escaped here used to
                # kill the thread, and the system's wall boxes froze for
                # good after its app restarted (Yaya, 2026-10-11).
                try:
                    self._pump(relay)
                except Exception as exc:                # noqa: BLE001
                    logger.warning("hub overlay %s: relay ended on %s: %s; "
                                   "reconnecting", self.slug,
                                   type(exc).__name__, exc)
                    relay.close()
                with self._lock:
                    self._relay = None
                if not self._stop.is_set():
                    self.mux.put(envelope(self.slug, reset=True))
            else:
                self._answer(status)
                if status in FINAL or not self.host:
                    self.final = True
                    self.mux.wake()
                    return
            failures += 1
            self._stop.wait(min(30.0, 1.5 * 2 ** min(failures, 5)))
        self._answer(0)

    def start(self):
        self.thread.start()

    def stop(self):
        """From any thread: no more reconnects, and a parked read wakes now."""
        self._stop.set()
        with self._lock:
            relay = self._relay
        if relay is not None:
            relay.close()
        self.first.set()


class Mux:
    """One page's stream: every upstream's events, through one bounded
    queue, out of events(). close() from any thread ends all of it."""

    def __init__(self, feeds, cookie=None, token=None):
        headers = {"Accept": "text/event-stream"}
        if cookie:
            headers["Cookie"] = masters_service.for_master(cookie)
        if token:
            headers["X-Session"] = token
        cams = sum(len(c) for _, c in feeds.values()) or 1
        self._queue = collections.deque(maxlen=QUEUE_PER_CAMERA * cams)
        self._cond = threading.Condition()
        self.closed = False
        self.upstreams = [Upstream(self, slug, url, c, headers)
                          for slug, (url, c) in feeds.items()]

    def start(self, wait=FIRST_WAIT_S):
        """Open every upstream; {slug: first status} once each has answered
        or `wait` seconds have gone (an unanswered one reads as 0)."""
        _register(self)
        for u in self.upstreams:
            u.start()
        for u in self.upstreams:
            u.first.wait(wait)
        return {u.slug: u.status or 0 for u in self.upstreams}

    def put(self, item):
        with self._cond:
            if self.closed:
                return
            self._queue.append(item)    # a full deque drops its oldest
            self._cond.notify_all()

    def wake(self):
        with self._cond:
            self._cond.notify_all()

    def _all_final(self):
        return all(u.final for u in self.upstreams)

    def events(self):
        """The response body. Ends when closed or every upstream gave up."""
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self.closed or self._queue
                                        or self._all_final(), KEEPALIVE_S)
                    if self.closed:
                        return
                    items = list(self._queue)
                    self._queue.clear()
                if not items and self._all_final():
                    return
                yield b"".join(items) if items else b": keepalive\n\n"
        finally:
            self.close()

    def close(self):
        """Idempotent, from any thread (the response's background task)."""
        with self._cond:
            if self.closed:
                return
            self.closed = True
            self._cond.notify_all()
        for u in self.upstreams:
            u.stop()
        with _LIVE_LOCK:
            if self in _LIVE:
                _LIVE.remove(self)


def _register(mux):
    with _LIVE_LOCK:
        _LIVE.append(mux)
        evicted = _LIVE[:-MAX_STREAMS]
    for old in evicted:
        old.close()


def live_count():
    with _LIVE_LOCK:
        return len(_LIVE)
