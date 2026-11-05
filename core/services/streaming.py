# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""The WebRTC video path: MediaMTX stream naming, health, and the WHEP proxy.

Video reaches a browser two ways in this product, chosen per system by
[app] stream in config.toml:

  "mjpeg"             -- the detection app JPEG-encodes each annotated frame
                         and serves a multipart stream the master relays into
                         an <img>. No extra service, works everywhere.
  "webrtc"  (default) -- the app's GStreamer pipeline draws the boxes, encodes
                         H.264 once, and publishes to MediaMTX over loopback
                         RTSP; the browser plays it with a <video> element.

The default became webrtc on 2026-10-09 (Tok Aba), for EVERY system that
does not say otherwise -- existing ones included, since no system ever wrote
the key (there is no UI for it). Until then every box ran MJPEG without
anyone having chosen it: the gateway sat installed with zero paths, and each
open preview held one of the browser's six connections per origin. An
explicit "mjpeg" is still respected; everything below that makes WebRTC
degrade to MJPEG when it cannot be served is unchanged and is what makes a
default this bold safe.

Both are kept because they fail differently. WebRTC is cheaper per viewer and
much lower latency, but it needs the gateway running, a working UDP path to
the browser and a codec the browser accepts; MJPEG needs nothing but HTTP and
has never met a browser it could not feed. A system whose WebRTC path is
broken should degrade to a working picture, not to no picture -- so `mode()`
below answers "mjpeg" whenever WebRTC cannot actually be served.

NAMING. One MediaMTX path per camera, "<slug>__<camera_id>": flat because
MediaMTX paths are flat, double-underscore because a single one is legal
inside both a slug and a camera id and would make the two halves ambiguous.

SECURITY. The gateway's signalling port is loopback-only; the browser reaches
it through the master's authenticated /api/detector/webrtc/... proxy, so
watching a camera requires a session exactly as the MJPEG relay does. Only
the media port is exposed, and media is DTLS-SRTP with ICE credentials that
come from the authenticated answer. See scripts/mediamtx/mediamtx.yml.
"""

import json
import re
import time
import urllib.error
import urllib.request

#: Where the gateway listens. Loopback by design -- see the module docstring.
GATEWAY_HOST = "127.0.0.1"
RTSP_PORT = 8554
WHEP_PORT = 8889
API_PORT = 9997

#: How long to wait on the gateway. Signalling is a local HTTP round trip on
#: the same box: if it has not answered in this long it is not going to.
TIMEOUT_S = 5.0

STREAM_MODES = ("mjpeg", "webrtc")
#: What a system with no [app] stream key asks for. The app
#: (systems/demo/app/run.py, "video transport") reads the same key with the
#: same default; the two MUST agree or the master offers a transport the app
#: is not publishing (pinned by tests/smoke_webrtc.py).
DEFAULT_MODE = "webrtc"
#: What every failure degrades to. Never anything else: the img works
#: everywhere, which is the whole point of keeping it.
FALLBACK_MODE = "mjpeg"

_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")
#: A gateway session id as MediaMTX mints them (a UUID); anything else is
#: refused before it can become part of a URL on the gateway.
_SESSION = re.compile(r"^[a-zA-Z0-9-]{8,64}$")


def path_name(slug, camera_id=""):
    """The MediaMTX path for one camera: "<slug>__<camera_id>".

    Both halves are sanitised the same way MediaMTX itself accepts, so a
    camera id minted from an operator's camera name ("Bay 3 · east") cannot
    produce a path the gateway will refuse, or -- worse -- one that escapes
    into another camera's URL.
    """
    slug = _SAFE.sub("-", str(slug or "system")).strip("-") or "system"
    cam = _SAFE.sub("-", str(camera_id or "main")).strip("-") or "main"
    return f"{slug}__{cam}"


def whep_url(slug, camera_id=""):
    """The gateway's WHEP endpoint for this camera (loopback; the master
    proxies it -- a browser must never be handed this address)."""
    return (f"http://{GATEWAY_HOST}:{WHEP_PORT}/"
            f"{path_name(slug, camera_id)}/whep")


def _api(path, timeout=TIMEOUT_S):
    """One GET against the gateway's control API, decoded. None if the
    gateway is not running -- which is a normal state, not an error: a box
    that never enabled WebRTC has no gateway at all."""
    url = f"http://{GATEWAY_HOST}:{API_PORT}/v3/{path.lstrip('/')}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def gateway_running():
    """Is the video gateway up on this box?"""
    return _api("config/global/get", timeout=2.0) is not None


#: Gateway source types that are a PULL the gateway makes itself -- a
#: [video] feed = "passthrough" camera (systems/demo/app/passthrough.py).
#: A legacy publish is "rtspSession" and is not in this list.
PULL_SOURCES = ("rtspSource", "rtspsSource")
#: After a passthrough camera's first-viewer pull FAILED (camera refused a
#: second session, unreachable, a codec this browser does not take), how
#: long its path is answered "not ready" -- so the page stays on MJPEG
#: instead of re-trying a doomed handshake on every 30 s rejoin.
PULL_FAIL_HOLD_S = 120.0
_pull_failed = {}
#: user:password@ in any URL in a text (the app's passthrough.mask rule).
_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s'\"<>]*@")


def stream_ready(slug, camera_id=""):
    """Is this camera's stream actually published and servable RIGHT NOW?

    Asked before offering WebRTC to a page: a path that exists but carries no
    tracks answers a WHEP offer with an error, and a viewer would see a dead
    <video> where the MJPEG path would have shown a picture.

    A passthrough path is the exception. It is an ON-DEMAND pull: the gateway
    opens the camera when the first viewer arrives, so it is never `ready`
    before somebody asks -- and nobody would ever ask if "not ready" meant
    "use MJPEG". Configured (its source is a pull) counts as servable, unless
    its last cold start failed within PULL_FAIL_HOLD_S.
    """
    name = path_name(slug, camera_id)
    info = _api(f"paths/get/{name}", timeout=2.0)
    if not info:
        return False
    if info.get("ready"):
        return True
    src = info.get("source") if isinstance(info.get("source"), dict) else {}
    return (src.get("type") in PULL_SOURCES
            and time.time() - _pull_failed.get(name, 0.0) > PULL_FAIL_HOLD_S)


def paths():
    """Every published path, for the health page. Empty without a gateway."""
    listed = _api("paths/list")
    return (listed or {}).get("items") or []


def mode(cfg, slug=None, camera_id=""):
    """Which transport this system's video should use: "webrtc" or "mjpeg".

    The config ASKS; this function answers with what can actually be served.
    A system set to webrtc on a box whose gateway is down (not installed,
    crashed, mid-restart) is told "mjpeg" so its pages show a picture rather
    than an explanation. `slug` is optional: pass it to also require that
    THIS camera's stream is live, which is the check a page wants; omit it
    to ask only whether the box can do WebRTC at all.
    """
    want = str(((cfg or {}).get("app") or {}).get("stream")
               or DEFAULT_MODE).strip().lower()
    if want not in STREAM_MODES:
        want = DEFAULT_MODE
    if want != "webrtc":
        return FALLBACK_MODE
    if not gateway_running():
        return FALLBACK_MODE
    if slug is not None and not stream_ready(slug, camera_id):
        return FALLBACK_MODE
    return "webrtc"


def valid_session(session):
    """Is this a session id the gateway could have minted?"""
    return bool(_SESSION.match(str(session or "")))


def session_of(location):
    """The session id in a gateway Location header ("/<path>/whep/<id>", or
    absolute), or "" when there is none or it is not shaped like one."""
    tail = str(location or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if valid_session(tail) else ""


def whep_offer(slug, camera_id, sdp_offer, timeout=TIMEOUT_S):
    """Hand one SDP offer to the gateway, return
    (status, sdp_answer_or_error, session_id).

    This is the whole browser handshake: the page describes what it can
    receive, the gateway answers with what it will send and where. Media
    then flows browser <-> gateway directly over the media port; nothing
    streams through this process, which is why a Python master can serve
    many WebRTC viewers without touching a video frame.

    The session id (from the gateway's Location header) is what whep_delete
    needs to release the session when the viewer leaves; "" on a failure.

    On a passthrough camera the FIRST viewer's offer is also the moment the
    gateway opens the camera, so this call waits for that RTSP connect. The
    gateway gives up after its sourceOnDemandStartTimeout (4 s, set by
    app/passthrough.py), inside `timeout` and inside the page's 6 s
    handshake, so a camera that refuses the pull fails here as an error and
    the page drops to MJPEG. Any failure is remembered for stream_ready.
    """
    if not isinstance(sdp_offer, (bytes, bytearray)):
        sdp_offer = str(sdp_offer or "").encode("utf-8")
    name = path_name(slug, camera_id)
    req = urllib.request.Request(
        whep_url(slug, camera_id), data=sdp_offer, method="POST",
        headers={"Content-Type": "application/sdp"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _pull_failed.pop(name, None)
            return (r.status, r.read().decode("utf-8"),
                    session_of(r.headers.get("Location")))
    except urllib.error.HTTPError as e:
        _pull_failed[name] = time.time()
        body = ""
        try:
            # Masked: this text goes to the browser, and the gateway's error
            # for a failed pull could quote the camera address it pulls.
            body = _USERINFO.sub(r"\1***@", e.read().decode("utf-8", "replace"))[:400]
        except Exception:                                   # noqa: BLE001
            pass
        # 404 from the gateway means "nothing is publishing that path" --
        # said plainly, because the operator's next question is always
        # "is detection actually running on that camera?"
        if e.code == 404:
            return 404, (f"no live stream for {path_name(slug, camera_id)} - "
                         "is detection running on this camera?"), ""
        return e.code, body or f"gateway answered {e.code}", ""
    except (urllib.error.URLError, OSError) as e:
        _pull_failed[name] = time.time()
        return 502, (f"video gateway is not answering on "
                     f"{GATEWAY_HOST}:{WHEP_PORT} ({e}) - is "
                     "irivai-mediamtx running?"), ""


def whep_delete(slug, camera_id, session, timeout=TIMEOUT_S):
    """Release one session on the gateway (the WHEP DELETE). True when the
    gateway said so; False for a session it no longer had or a gateway that
    is not answering -- both mean the session is not running, which is all
    the caller wanted."""
    if not valid_session(session):
        return False
    req = urllib.request.Request(f"{whep_url(slug, camera_id)}/{session}",
                                 method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False
