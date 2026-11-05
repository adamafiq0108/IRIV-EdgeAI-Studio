# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""passthrough.py -- the live VIEW straight from an IP camera, not through us.

[video] feed in the system's config:

  "legacy"       (default) the app decodes the camera, paints or copies the
                 frame, encodes H.264 (openh264, software) and publishes it
                 to the gateway per camera (app/publisher.py). Exactly what
                 every system did before this module existed.
  "passthrough"  for an RTSP camera, the gateway PULLS the camera's main
                 stream itself and hands it to the browser over WebRTC. The
                 app registers that pull and does nothing else for the view:
                 no decode, no paint, no copy, no encode. Detection keeps
                 decoding its own (sub-)stream exactly as before.

WHY. On a 4-core CM5 the per-camera decode, shrink, paint and openh264
encode for the view is most of what limits a box to a handful of cameras.
With [video] overlay = "client" the browser already draws the boxes, so the
picture no longer needs to pass through this process at all.

ELIGIBILITY. Passthrough needs overlay = "client" (the camera's own picture
has no boxes burned in), an rtsp:// detect source, and a main-stream address
-- `view_source` on the camera entry, or derived here from the detect URL by
the path pairs the common brands use (decide()). Anything else stays legacy
and says why, once, in the log and in the camera's state.

MECHANISM. One path per camera on the gateway's control API (loopback
127.0.0.1:9997), named exactly as the legacy publish is
("<slug>__<camera>", core/services/streaming.py path_name), with the camera
URL as its `source` and `sourceOnDemand`: the gateway opens the camera only
while somebody watches and closes it `sourceOnDemandCloseAfter` after the
last viewer leaves. Option names are MediaMTX v1.20.1's (the version
scripts/mediamtx/install.sh pins): `rtspTransport`, not the pre-1.x
`sourceProtocol`.

SECRETS. The camera URL carries its user:password. It is held in THIS
process's memory and in the gateway's (sent once, in a POST body over
loopback) and nowhere else: never logged (mask() on every text that could
hold it, including the gateway's own error replies), never put on a command
line, never written to a file or to state.json, never returned. The status
this module reports names a feed and a reason, never an address.

Stdlib only, no app imports: tests/smoke_passthrough*.py drive it against a
stub gateway on loopback.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

FEEDS = ("legacy", "passthrough")
DEFAULT_FEED = "legacy"

#: The gateway's control API. Loopback only (scripts/mediamtx/mediamtx.yml
#: apiAddress); the yml's authInternalUsers grant the `api` action (and every
#: other) to 127.0.0.1/::1 only, without credentials, which is what lets this
#: work with no secret of its own.
API_BASE = "http://127.0.0.1:9997/v3"
#: A loopback call that has not answered in this long is a gateway that is
#: not going to. Short, because sync() runs on the detection loop.
API_TIMEOUT_S = 1.5

#: How long the gateway waits for the camera on the FIRST viewer before it
#: gives up and fails that viewer's WHEP request. Under the master's 5 s WHEP
#: wait (core/services/streaming.py TIMEOUT_S) and the page's 6 s handshake
#: (WebRTCVideo.jsx HANDSHAKE_TIMEOUT_MS), so a camera that refuses the pull
#: costs the page ~4 s before it drops to MJPEG, never a hang.
START_TIMEOUT = "4s"
#: How long the pull is kept after the last viewer leaves. A viewer switching
#: tabs and back within this rejoins a running pull instead of a cold one.
CLOSE_AFTER = "10s"

#: A gateway path name we will put into an API URL. path_name() produces only
#: these characters; anything else is refused, and the name is URL-quoted
#: anyway.
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")

#: user:password@ in ANY URL inside a text. Greedy to the LAST "@" in the
#: token -- "admin:P@ss@cam" is a working camera address (faults.redact notes
#: the same), and a password typed with a raw "/" in it must not survive
#: either. Over-masking a host in a log line costs nothing; under-masking
#: publishes a password.
_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s'\"<>]*@")


def mask(text):
    """`text` with every URL's user:password replaced by ***. For anything
    that could carry a camera address on its way to a log line."""
    return _USERINFO.sub(r"\1***@", str(text if text is not None else ""))


def path_name(slug, camera_id=""):
    """The gateway path for one camera. MUST equal core/services/streaming.py
    path_name() and run.py stream_url() (pinned by tests/smoke_passthrough.py):
    the passthrough path takes the legacy publish's place under the same name,
    so the master and the page need no change to find it."""
    slug = _SAFE.sub("-", str(slug or "system")).strip("-") or "system"
    cam = _SAFE.sub("-", str(camera_id or "main")).strip("-") or "main"
    return f"{slug}__{cam}"


def feed_setting(cfg):
    """"legacy" or "passthrough" from [video] feed. Anything unreadable is
    the default: a typo must cost the new mode, never the picture."""
    video = (cfg or {}).get("video") or {}
    if not isinstance(video, dict):
        video = {}
    feed = str(video.get("feed") or DEFAULT_FEED).strip().lower()
    return feed if feed in FEEDS else DEFAULT_FEED


# -- the camera's main stream --------------------------------------------------

def _rtsp_parts(url):
    """urlsplit of a usable rtsp:// URL, else None. Whitespace and control
    characters are refused outright: they cannot be in a camera address and
    could be in an attempt to smuggle a second line into something."""
    s = str(url or "").strip()
    if not s or any(ord(ch) < 33 or ord(ch) == 127 for ch in s):
        return None
    try:
        parts = urllib.parse.urlsplit(s)
        host = parts.hostname
        parts.port                                      # raises on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() != "rtsp" or not host:
        return None
    return parts


def is_rtsp(source):
    """Is this camera source an rtsp:// address? (USB indexes, GigE, depth,
    file:, gst: and another system's shared feed are not.)"""
    return _rtsp_parts(source) is not None


#: Sub-stream -> main-stream, by the path conventions of the brands this
#: product meets (frontend/src/lib/rtspStream.js offers the same pairs on the
#: camera form). Each entry: (pattern on the path or query, replacement).
#: A pattern that matches the MAIN form instead means the detect URL already
#: IS the main stream, and the view pulls that same address.
_SUB_TO_MAIN = (
    # Hikvision / Hilook: /Streaming/Channels/<ch>02 -> <ch>01 (102 -> 101,
    # 202 -> 201). Case varies by firmware.
    ("path", re.compile(r"(?i)^(.*/streaming/channels/\d*?)02/?$"), r"\g<1>01"),
    # Older Hikvision: /h264/ch1/sub/av_stream -> /main/.
    ("path", re.compile(r"(?i)^(.*/ch\d+/)sub(/av_stream)$"), r"\g<1>main\g<2>"),
    # TP-Link / Tapo: /stream2 -> /stream1.
    ("path", re.compile(r"(?i)^(/?)stream2/?$"), r"\g<1>stream1"),
    # Dahua / Amcrest: ...?channel=1&subtype=1 -> subtype=0.
    ("query", re.compile(r"(?i)(^|&)(subtype=)1(?=&|$)"), r"\g<1>\g<2>0"),
)
_ALREADY_MAIN = (
    ("path", re.compile(r"(?i)/streaming/channels/\d*?01/?$")),
    ("path", re.compile(r"(?i)/ch\d+/main/av_stream$")),
    ("path", re.compile(r"(?i)^/?stream1/?$")),
    ("query", re.compile(r"(?i)(^|&)subtype=0(&|$)")),
)


def main_stream(detect_url):
    """(main-stream URL, "") for a detect URL on a known sub-stream path,
    (the same URL, "") when it already reads the main stream, or
    (None, why) when this camera's main stream is not known.

    Credentials, host, port and the rest of the query ride along unchanged:
    the main stream is on the same camera behind the same login.
    """
    parts = _rtsp_parts(detect_url)
    if parts is None:
        return None, "not an RTSP camera"
    for where, pat, repl in _SUB_TO_MAIN:
        field = parts.path if where == "path" else parts.query
        new, n = pat.subn(repl, field, count=1)
        if n:
            parts = parts._replace(**{where: new})
            return urllib.parse.urlunsplit(parts), ""
    for where, pat in _ALREADY_MAIN:
        if pat.search(parts.path if where == "path" else parts.query):
            return str(detect_url).strip(), ""
    return None, "no main stream known for this camera path - set view_source"


def view_url(entry):
    """(URL the gateway should pull, "") or (None, why) for one camera entry.

    An explicit `view_source` wins, but only on the SAME HOST as the detect
    source: a camera re-pointed at another address keeps whatever
    view_source was typed for the old one, and showing the old camera's
    picture under the new camera's boxes is worse than no passthrough. It
    also keeps the gateway pulling only from a camera the system already
    reads (no new destination comes from anywhere but this system's config).
    """
    entry = entry if isinstance(entry, dict) else {}
    src = str(entry.get("source") or "").strip()
    detect = _rtsp_parts(src)
    if detect is None:
        return None, "not an RTSP camera"
    explicit = str(entry.get("view_source") or "").strip()
    if explicit:
        view = _rtsp_parts(explicit)
        if view is None:
            return None, "view_source is not an rtsp:// address"
        if (view.hostname or "").lower() != (detect.hostname or "").lower():
            return None, "view_source is on another host than the camera"
        return explicit, ""
    return main_stream(src)


def decide(feed, overlay_client, stream_mode, entry, gst_mode=False,
           parked=False):
    """("passthrough", "", url) or ("legacy", why, None) for one camera.

    Pure: the caller hands in what it already knows. `why` is a short reason
    an operator can act on; it never contains the address.
    """
    if feed != "passthrough":
        return "legacy", "", None
    if not overlay_client:
        # No burned-in boxes exist on the camera's own picture, so an
        # overlay-less view is all passthrough could give a server-mode
        # system. Legacy, said plainly, rather than that.
        return "legacy", "overlay is server", None
    if stream_mode != "webrtc":
        return "legacy", "stream is mjpeg", None
    if gst_mode:
        return "legacy", "the gstreamer detector owns the camera", None
    if parked:
        return "legacy", "camera is off", None
    url, why = view_url(entry)
    if url is None:
        return "legacy", why, None
    return "passthrough", "", url


def path_conf(url):
    """The gateway path configuration for a passthrough camera (v1.20.1
    option names, durations as the API's strings)."""
    return {"source": url,
            "sourceOnDemand": True,
            "sourceOnDemandStartTimeout": START_TIMEOUT,
            "sourceOnDemandCloseAfter": CLOSE_AFTER,
            # TCP: interleaved over the one RTSP connection. UDP from a
            # camera is a second port pair per stream through whatever sits
            # between, and the publish side of this gateway is TCP already.
            "rtspTransport": "tcp"}


# -- the gateway's control API -------------------------------------------------

class Gateway:
    """The handful of MediaMTX v3 API calls passthrough needs. Never raises:
    every failure is (status, masked text), status 0 for "not answering"."""

    def __init__(self, base=None, timeout=API_TIMEOUT_S):
        # Read at construction, not at definition, so a test can point the
        # module's API_BASE at a stub before the app builds its Views.
        self.base = str(base or API_BASE).rstrip("/")
        self.timeout = float(timeout)

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:                               # noqa: BLE001
                text = ""
            # The gateway's error can quote the source it was given; masked
            # before anything else sees it.
            return e.code, mask(text)[:300]
        except (urllib.error.URLError, OSError, ValueError) as e:
            return 0, mask(getattr(e, "reason", e))[:300]

    @staticmethod
    def _q(name):
        if not _NAME.match(str(name or "")):
            raise ValueError("not a gateway path name")
        return urllib.parse.quote(str(name), safe="")

    def put(self, name, url):
        """Register (or re-point) one passthrough path. (ok, why)."""
        try:
            q = self._q(name)
        except ValueError as e:
            return False, str(e)
        conf = path_conf(url)
        status, text = self._call("POST", f"/config/paths/add/{q}", conf)
        if status == 200:
            return True, ""
        if status == 400 and "already exists" in text.lower():
            status, text = self._call("POST", f"/config/paths/replace/{q}", conf)
            if status == 200:
                return True, ""
        return False, (f"gateway answered {status}: {text}" if status
                       else f"gateway not answering ({text})")

    def delete(self, name):
        """Drop one path's configuration. True when it is gone (a path the
        gateway did not have counts: gone is what the caller wanted)."""
        try:
            q = self._q(name)
        except ValueError:
            return False
        status, _text = self._call("DELETE", f"/config/paths/delete/{q}")
        return status in (200, 404)

    def pulls(self):
        """{name: source type} of every RUNTIME path whose source is a pull
        configured through the API, or None when the gateway is down.

        The runtime list, not config/paths/list: it says the same thing about
        which pulls exist, and it carries a source TYPE rather than the source
        URL, so the credentials of every system's cameras are not read back
        into this process for a liveness check.
        """
        status, text = self._call("GET", "/paths/list?itemsPerPage=1000")
        if status != 200:
            return None
        try:
            items = (json.loads(text or "{}") or {}).get("items") or []
        except ValueError:
            return None
        out = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "")
            src = it.get("source") if isinstance(it.get("source"), dict) else {}
            # confName == name: configured for exactly this name, not caught
            # by all_others (that is a legacy publish).
            if name and it.get("confName") == name:
                out[name] = str(src.get("type") or "")
        return out


# -- one app's passthrough paths -------------------------------------------------

class Views:
    """Which of this system's cameras the gateway pulls, kept in step.

    sync() on start and on every config reload; tick() on the app's existing
    5 s balance check; close() on exit. The URLs live in `_held` and nowhere
    else.
    """

    def __init__(self, slug, gateway=None, log=None):
        self.slug = str(slug or "system")
        self.gw = gateway or Gateway()
        self.log = log
        self._held = {}         # camera id -> (path name, url)
        self._pending = {}      # camera id -> (path name, url): put failed
        self.status = {}        # camera id -> {"feed", "why"}

    def _say(self, level, msg, *args):
        if self.log is not None:
            getattr(self.log, level)(mask(msg % args if args else msg))

    def _put(self, cid, name, url):
        ok, why = self.gw.put(name, url)
        if ok:
            self._held[cid] = (name, url)
            self._pending.pop(cid, None)
            return True, ""
        self._pending[cid] = (name, url)
        return False, why

    def sync(self, wanted, sweep=False):
        """Bring the gateway in line with `wanted` ({camera id: decide()}).
        Returns the per-camera status. A camera no longer listed, or listed
        as legacy, has its path deleted; one listed as passthrough has its
        path added (or re-pointed when its URL changed).

        `sweep` (the app's first sync only): also delete a pull left under a
        LEGACY camera's name by a run that did not exit cleanly. The gateway
        refuses a publish on a path that has a pull configured, so without
        this a crash in passthrough followed by a switch back to legacy left
        that camera with no WebRTC until the gateway restarted. One loopback
        list per app start; never on a reload."""
        status = {}
        pulls = None if sweep else {}
        for cid in list(self._held) + list(self._pending):
            feed = (wanted.get(cid) or ("legacy",))[0]
            if feed != "passthrough":
                held = self._held.pop(cid, None) or self._pending.pop(cid, None)
                self._pending.pop(cid, None)
                if held and not self.gw.delete(held[0]):
                    self._say("warning", "[VIEW] %s: could not delete the "
                              "passthrough path (the gateway will drop it on "
                              "its next restart)", cid)
        for cid, (feed, why, url) in wanted.items():
            name = path_name(self.slug, cid)
            if feed == "passthrough":
                if (self._held.get(cid) or (None, None))[1] == url:
                    status[cid] = {"feed": "passthrough", "why": ""}
                    continue
                ok, err = self._put(cid, name, url)
                if not ok:
                    self._say("warning", "[VIEW] %s: passthrough path not "
                              "taken (%s) - MJPEG until it is", cid, err)
                status[cid] = {"feed": "passthrough",
                               "why": "" if ok else "waiting for the video gateway"}
                continue
            # Legacy. See `sweep` for the pull a crashed run left behind.
            if pulls is None:
                pulls = self.gw.pulls() or {}
            if name in pulls:
                self.gw.delete(name)
            status[cid] = {"feed": "legacy", "why": why}
        for cid, st in status.items():
            # A plain legacy camera (the default, nothing asked) says
            # nothing: a system that never set [video] feed logs as before.
            if self.status.get(cid, {"feed": "legacy", "why": ""}) != st:
                self._say("info", "[VIEW] %s: %s%s", cid, st["feed"],
                          f" ({st['why']})" if st["why"] else "")
        self.status = status
        return status

    def tick(self):
        """Re-ensure on the periodic check: one loopback list. A gateway that
        restarted forgot every path added through its API (they live in its
        memory, by design); this puts them back. Also retries a failed put."""
        if not self._held and not self._pending:
            return
        pulls = self.gw.pulls()
        if pulls is None:
            return
        for cid, (name, url) in list(self._held.items()):
            if name not in pulls:
                self._held.pop(cid, None)
                ok, err = self._put(cid, name, url)
                self._say("info" if ok else "warning",
                          "[VIEW] %s: passthrough path %s", cid,
                          "restored" if ok else f"not restored ({err})")
        for cid, (name, url) in list(self._pending.items()):
            if self._put(cid, name, url)[0]:
                self.status[cid] = {"feed": "passthrough", "why": ""}
                self._say("info", "[VIEW] %s: passthrough path taken", cid)

    def passthrough(self, cid):
        """Is this camera's view pulled by the gateway (or about to be)?"""
        return cid in self._held or cid in self._pending

    def close(self):
        """Delete every path this app registered. On exit and before the
        legacy publisher may take a name back."""
        for cid, (name, _url) in list(self._held.items()):
            self.gw.delete(name)
        self._held.clear()
        self._pending.clear()
