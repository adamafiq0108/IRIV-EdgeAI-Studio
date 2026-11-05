# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
camera.py -- frame capture and JPEG encoding, with a hardware-free fallback.

Three backends, chosen at runtime by what is installed and what config asks
for: cv2 (USB index or RTSP URL), PIL (synthetic frames), and a last-resort
null backend that yields nothing rather than crashing.

**One reader per camera.** Most capture hardware refuses a second one, so the
detection loop owns the camera and everything else -- the MJPEG stream, the
snapshots, the agent's /video_feed -- reads the frame this module last
produced. An adapter or a stream handler that opens the device itself is the
single most common way to break live detection.
"""

import glob
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import time

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

# The depth camera. Imported for the source test alone -- the SDK behind it is
# loaded only when a camera actually asks for "orbbec", so a box without the
# hardware (or without the wheel) carries nothing extra.
try:
    from . import orbbec
except ImportError:                     # run as a loose script, not a package
    import orbbec

# Camera failures are the ones an operator chases, and until this module
# logged they existed only as prints in app.log, which no page reads.
try:
    from . import faults
except ImportError:                     # run as a loose script, not a package
    import faults

log = faults.logger(__name__)


# ---------------------------------------------------------------------------
# FFmpeg decoder settings, for the sources OpenCV opens with CAP_FFMPEG
# (RTSP, HTTP, file:). USB/V4L2, GigE and GStreamer captures never read them.
#
# Left alone, OpenCV gives every FFmpeg capture one decoder thread per CPU, so
# four RTSP cameras on a 4-core CM5 run sixteen decoder threads fighting the
# detection loop for the same four cores. Two per camera is plenty for a
# 640-class stream.
#
# The thread count is OPENCV_FFMPEG_THREADS, NOT a "threads" entry in
# OPENCV_FFMPEG_CAPTURE_OPTIONS: OpenCV hands that option string to the
# demuxer only (avformat_open_input) and opens the decoder with no options, so
# "threads;2" there is silently ignored. OPENCV_FFMPEG_THREADS exists from
# OpenCV 4.7; older builds (Debian bookworm's python3-opencv is 4.6) ignore it
# and keep one thread per CPU -- nothing in OpenCV can change that there.
#
# OPENCV_FFMPEG_CAPTURE_OPTIONS is set only when [camera].ffmpeg_options asks.
# Setting it at all REPLACES OpenCV's own default (rtsp_flags=prefer_tcp: TCP
# first, UDP if the camera refuses), so a string that names no transport gets
# prefer_tcp put back in front -- an operator adding a timeout must not
# silently move every camera to UDP. Both variables are read by OpenCV at
# each open, so setting them before the first VideoCapture is enough.
# ---------------------------------------------------------------------------

#: Decoder threads per FFmpeg capture when [camera].ffmpeg_threads is absent.
FFMPEG_THREADS = 2

FFMPEG_THREADS_ENV = "OPENCV_FFMPEG_THREADS"
FFMPEG_OPTIONS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"


def apply_ffmpeg_env(section, environ=None):
    """Export the [camera] FFmpeg settings for this process, once, at start.

    `section` is the [camera] table. `ffmpeg_threads` (default 2, 0 = leave
    OpenCV's per-CPU default) and `ffmpeg_options` (OpenCV's "key;value|..."
    form, default "" = leave FFmpeg alone). A variable the operator already
    exported wins over both and is never touched. Logs one line and returns
    it, so a test can read what was decided without a log handler.
    """
    env = os.environ if environ is None else environ
    section = section if isinstance(section, dict) else {}
    said = []

    raw = section.get("ffmpeg_threads", FFMPEG_THREADS)
    try:
        threads = max(0, int(raw))
    except (TypeError, ValueError):
        threads = FFMPEG_THREADS
        said.append(f"ffmpeg_threads={raw!r} is not a number, using {threads}")
    if env.get(FFMPEG_THREADS_ENV):
        said.append(f"{FFMPEG_THREADS_ENV}={env[FFMPEG_THREADS_ENV]} already "
                    f"exported, left alone")
    elif threads:
        env[FFMPEG_THREADS_ENV] = str(threads)
        said.append(f"{FFMPEG_THREADS_ENV}={threads}")
    else:
        said.append("decoder threads left at OpenCV's default")

    options = str(section.get("ffmpeg_options") or "").strip()
    if env.get(FFMPEG_OPTIONS_ENV):
        said.append(f"{FFMPEG_OPTIONS_ENV}={env[FFMPEG_OPTIONS_ENV]!r} already "
                    f"exported, left alone")
    elif options:
        keys = {pair.split(";", 1)[0].strip() for pair in options.split("|")}
        if not keys & {"rtsp_transport", "rtsp_flags"}:
            options = "rtsp_flags;prefer_tcp|" + options
        env[FFMPEG_OPTIONS_ENV] = options
        said.append(f"{FFMPEG_OPTIONS_ENV}={options!r}")

    line = "ffmpeg capture: " + "; ".join(said)
    log.info(line)
    return line


# ---------------------------------------------------------------------------
# Camera sharing across processes.
#
# One raw V4L2 camera has ONE reader, but several systems may want the same
# camera. The app that opens the device registers {source -> its stream port}
# here; another app that finds the device busy asks shared_feed_source() and
# consumes the owner's /raw MJPEG over loopback instead. Registry is runtime
# state (pid-checked), so a crashed owner's entry is simply ignored.
# ---------------------------------------------------------------------------

_CAM_REGISTRY = os.path.join(tempfile.gettempdir(), "cytron_cameras.json")


def reg_key(source):
    """The registry's key for a camera source. A USB index stays as it is
    (the numeric alias in shared_feed_source reads it, and it is no secret);
    anything else is a hash of the address. The file is in /tmp and readable
    by every local user, and keyed by the raw URL it held every RTSP
    camera's password (Pipi L1, 2026-10-11). Consumers only ever look up
    their OWN source, so a hash finds the same entry. core/services/
    detector_control.py _shared_feed_jpeg keeps the same rule."""
    s = str(source)
    return s if s.isdigit() else "h:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def _reg_entry(reg, source):
    """`source`'s entry, by its key, or under its raw address as an app from
    before 1.0.37 still running beside this one registered it."""
    return reg.get(reg_key(source)) or reg.get(str(source))


def _reg_load():
    try:
        with open(_CAM_REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _reg_update(mutate):
    """Read-modify-write the registry under an EXCLUSIVE file lock.

    Every detection app on the box writes this one JSON file. The old
    load-modify-save had no lock, so two apps saving within the same
    moment lost one app's change -- watched live: an owner's camera entry
    appeared and vanished within seconds as other apps' heartbeats
    clobbered it, and the system waiting on that entry never got its
    shared feed ("Camera connecting" forever on a multi-camera page).
    flock serialises the writers; readers stay lock-free (a torn read is
    impossible -- the write itself is still an atomic os.replace).

    `mutate(reg)` edits the dict in place; return False from it to skip
    the write. Without fcntl (not Linux) it falls back to the unlocked
    behaviour, which a dev laptop running ONE app never notices.
    """
    try:
        import fcntl                                        # noqa: PLC0415
    except ImportError:
        fcntl = None
    lock_fd = None
    if fcntl is not None:
        try:
            lock_fd = os.open(_CAM_REGISTRY + ".lock",
                              os.O_CREAT | os.O_RDWR, 0o666)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError:
            lock_fd = None
    try:
        reg = _reg_load()
        if mutate(reg) is False:
            return reg
        # An entry under a raw address (an app from before 1.0.37) goes as
        # soon as its owner is gone: it is the camera's password, in /tmp.
        for key in [k for k in reg if not (k.startswith(("_", "h:")) or k.isdigit())]:
            entry = reg.get(key)
            if not (isinstance(entry, dict) and _pid_alive(entry.get("pid"))):
                reg.pop(key, None)
        try:
            tmp = _CAM_REGISTRY + f".{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reg, f)
            os.replace(tmp, _CAM_REGISTRY)
        except OSError:
            pass
        return reg
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)               # closing releases the flock
            except OSError:
                pass


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def register_camera(source, stream_port, camera_id=""):
    """This process now OWNS `source` and re-serves it at /raw on stream_port.

    `camera_id` matters once a system runs several cameras: they all share ONE
    port, so the id is what tells the two apart on it. Without it the second
    camera would re-serve the first one's picture.
    """
    entry = {"port": int(stream_port), "pid": os.getpid()}
    if camera_id:
        entry["camera"] = str(camera_id)

    def _set(reg):
        reg[reg_key(source)] = entry
    _reg_update(_set)


def unregister_camera(source):
    def _drop(reg):
        entry = reg.get(reg_key(source))
        if not (entry and entry.get("pid") == os.getpid()):
            return False
        reg.pop(reg_key(source), None)
    _reg_update(_drop)


def shared_feed_source(source):
    """A URL for `source`'s live frames served by ANOTHER running app, or None.
    This is what lets two systems run on one camera: the second consumes the
    first one's unannotated /raw feed instead of fighting for the device --
    ONE grab and one decode of the physical camera, however many systems
    watch it.

    A NUMERIC source that has no exact owner matches any other numeric
    owner. A USB camera's index wanders between /dev/video nodes when it
    re-enumerates, so "the owner of 0" and "a consumer configured 1" are
    usually the same physical camera mid-wander -- and treating the miss as
    "nobody owns it" made both systems fight over the hardware instead of
    sharing the one decode. Two DISTINCT USB cameras are already distinct
    entries; the alias only fires when the exact index has no owner at all.
    """
    reg = _reg_load()
    entry = _reg_entry(reg, source)
    if (not entry or entry.get("pid") == os.getpid()
            or not _pid_alive(entry.get("pid"))):
        entry = None
        # With slot symlinks installed (udev, /dev/cam-usb<n>) a numeric
        # source is CANONICAL -- "0" and "1" are two different sockets with
        # two different cameras -- so the wander alias must not fire: it
        # would quietly serve socket 1's system the socket-0 picture.
        if (str(source).isdigit()
                and not os.path.islink(f"/dev/cam-usb{source}")):
            for key, cand in reg.items():
                if (key.isdigit() and key != str(source)
                        and isinstance(cand, dict)
                        and cand.get("pid") != os.getpid()
                        and _pid_alive(cand.get("pid"))):
                    entry = cand
                    break
    if not entry:
        return None
    url = f"http://127.0.0.1:{entry.get('port')}/raw"
    cam_id = entry.get("camera")
    # One port serves every camera of that system; say which one is wanted.
    return f"{url}?camera={cam_id}" if cam_id else url


# -- chip load balancing ------------------------------------------------------
# Every detection app heartbeats here; the ones using the Hailo count towards
# the accelerator's load. Each app divides its target FPS by that count, so two
# systems sharing the chip settle at half rate each instead of one starving the
# other. Entries go stale (pid dead or heartbeat old) and stop counting.

_APP_TTL_S = 20

#: Seconds without a GOOD frame before a live camera is closed and
#: reopened, and the shortest gap between two such attempts.
#:
#: TIME, not a count of consecutive failures. The failure counter resets
#: on every successful read, and a stalled USB camera still delivers the
#: odd frame between its 10s select() timeouts -- so the count never
#: accumulated and the recovery never fired, while the feed sat on one
#: picture for minutes. "Nothing good for N seconds" cannot be reset by
#: a straggler.
_REOPEN_AFTER_STALL_S = 8.0

#: DCT-domain decode reductions libjpeg offers, largest first. A frame decoded
#: at 1/4 is not "decoded then shrunk": the IDCT simply reconstructs a quarter
#: of the coefficients, so it costs a fraction of the full decode AND the
#: resize that used to follow it -- measured 8 ms against 43 ms for a 5MP
#: frame on a CM5. See _dct_reduction.
_DCT_REDUCTIONS = (8, 4, 2)


def _dct_reduction(side, limit):
    """The largest of 1/2, 1/4, 1/8 that still leaves `side` at least `limit`.

    1 when no reduction fits -- a frame that is not much bigger than the
    working size is not worth the round trip through the raw buffer, and one
    that is already small has nothing to reduce. The reduced frame may land a
    few pixels OVER the limit (2592/4 = 648 for a 640 limit); _downscale
    takes it the rest of the way, which on a 648px frame is nothing.
    """
    try:
        side, limit = int(side), int(limit)
    except (TypeError, ValueError):
        return 1
    if side <= 0 or limit <= 0 or side < 2 * limit:
        return 1
    for r in _DCT_REDUCTIONS:
        if side / r >= limit:
            return r
    return 1


def _reduced_flag(reduce):
    """The imdecode flag for a reduction, or None when cv2 lacks it."""
    if cv2 is None:
        return None
    return getattr(cv2, f"IMREAD_REDUCED_COLOR_{int(reduce)}", None)


def _fourcc_name(cap):
    """The four-character code the device negotiated, e.g. "MJPG", or "".

    Read BACK rather than assumed: _apply_capture_settings asks for MJPG, and
    a camera without it keeps its default (YUYV), for which the raw path would
    hand the grab loop an uncompressed frame it cannot imdecode.
    """
    try:
        code = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    except Exception:                                       # noqa: BLE001
        return ""
    if code <= 0:
        return ""
    try:
        return code.to_bytes(4, "little").decode("ascii", "ignore").strip("\x00 ")
    except (OverflowError, ValueError):
        return ""


#: The same bar for a BIG uncompressed camera, which stalls for reasons that
#: clear on a reopen (see the buffer note in _apply_capture_settings) rather
#: than because anything is unplugged. Short, because the wait IS the freeze.
_BIG_STALL_S = 3.0

#: However short that bar, reopening is a USB re-negotiation -- so a camera
#: that is genuinely gone is retried on this pace, not on the bar.
_REOPEN_PACE_S = 5.0

#: A camera is COLLAPSED when it delivers less than this fraction of the
#: rate it agreed to, or fewer than _COLLAPSE_FPS frames a second,
#: continuously for _COLLAPSE_S. Both bars are low on purpose: dim light
#: legitimately takes a UVC camera to 7-8fps through auto-exposure, and
#: reopening a camera that is merely dark makes the picture worse, not
#: better. 0.5fps -- one frame every two seconds -- is not light.
_COLLAPSE_FRACTION = 0.15
_COLLAPSE_FPS = 2.0
_COLLAPSE_S = 10.0

#: Stalled reconnects to a shared feed before a consumer gives up on it
#: and opens the camera itself. Low: the upstream either serves frames
#: or it does not, and a consumer waiting politely on a feed nobody is
#: producing is the deadlock this exists to break.
_CONSUMER_GIVE_UP = 2

#: Asked of a camera to find its ceiling: no UVC device offers this, so
#: whatever the driver clamps it to IS the fastest it can do in the mode
#: just negotiated.
_FPS_PROBE = 240.0

#: The most we will ask a device for. Past this the USB bus and the
#: decoder pay for frames no detection looks at.
_MAX_DEVICE_FPS = 60.0

#: Past this many bytes per frame a camera is "big": the driver gets a few
#: buffers instead of one, so a late grab does not stall the stream. 1 MB is
#: comfortably above any compressed frame and below every uncompressed mode
#: worth worrying about (640x480 YUYV is 0.6 MB, 1456x1088 is 3.1 MB).
_BIG_FRAME_BYTES = 1_000_000


def heartbeat_app(uses_chip, weight=1):
    """Report this app as alive. `weight` is how many CAMERAS it inferences --
    a four-camera system does four times the work of a one-camera system, so
    counting processes would hand it four times the share of the chip."""
    def _beat(reg):
        apps = reg.setdefault("_apps", {})
        apps[str(os.getpid())] = {"ts": time.time(), "chip": bool(uses_chip),
                                  "weight": max(1, int(weight or 1))}
        # Prune the dead so the file stays small and counts stay honest.
        for pid in list(apps):
            entry = apps[pid]
            if (not _pid_alive(pid)
                    or (time.time() - entry.get("ts", 0)) > _APP_TTL_S * 3):
                apps.pop(pid, None)
    _reg_update(_beat)


def active_chip_apps():
    """How many live CAMERAS are being inferenced on the accelerator right now.

    Counted in cameras rather than processes: the budget is divided by this, and
    one process running four cameras consumes four cameras' worth of chip.
    An entry without a weight is an older app -- it counts as the one camera it
    could only have had.
    """
    apps = _reg_load().get("_apps") or {}
    now = time.time()
    return sum(max(1, int(e.get("weight", 1) or 1)) for pid, e in apps.items()
               if e.get("chip") and _pid_alive(pid)
               and (now - e.get("ts", 0)) <= _APP_TTL_S) or 1


#: Ceiling on the rate a FILE is played and fed to detection at. High enough
#: that ordinary footage (24, 25, 30fps) plays whole; low enough that a
#: 120fps clip cannot ask more of the chip than any camera on this box does.
MAX_FILE_FPS = 30.0


# ---------------------------------------------------------------------------
# Why a camera would not open.
#
# cv2.VideoCapture answers one bit -- isOpened() -- whatever went wrong. A USB
# camera that is unplugged, one this process may not read, and one another
# program holds all come back False; so do an IP camera that is switched off,
# one that rejected the password and one whose stream path is wrong, while the
# reason FFmpeg knew went to stderr and nowhere else. Each sends the installer
# to a different job (a cable, a permission, the camera's own web page), so
# the card needs the cause, not "cannot open camera". These ask the question
# again, directly, and only after a failure: a camera that opens pays nothing.
#
# The answers are short FACTS in fixed wording. The dashboard turns them into
# the operator's sentence (frontend/src/lib/camErrors.js matches on them), so a
# phrase changed here must change there too; tests/smoke_camcause.py pins
# both. Mirrored in core/services/detector_control.diagnose_open for the
# "Preview snapshot" path -- duplicated on purpose, the app must not import
# core/.
# ---------------------------------------------------------------------------

#: A probe is a round trip to a camera that has just failed, so it is bounded
#: hard -- and remembered, because the reconnect ladder and the 10 s reclaim
#: loop retry a dead camera for ever, and re-probing on every retry would add
#: seconds to each of them.
_PROBE_TIMEOUT_S = 2.0
_DIAG_TTL_S = 30.0
#: ...except a rejected password, which is held for ten minutes. Each probe of
#: it is one more failed login on top of FFmpeg's own, every 30 s, and IP
#: cameras lock an account out after a handful. The cache key is the source
#: URL, credentials included, so typing a new password asks again at once.
_REJECTED_TTL_S = 600.0
_REJECTED = "username or password rejected"
_diag_cache = {}


def _usb_node(source):
    """The /dev/video node a numeric source opens, or None (same symlink rule
    as Camera._capture_spec)."""
    s = str(source).strip()
    if not s.isdigit():
        return None
    link = f"/dev/cam-usb{s}"
    return os.path.realpath(link) if os.path.islink(link) else f"/dev/video{s}"


def _device_holder(node):
    """(name, pid) of ANOTHER process holding `node` open, or None. Read from
    /proc; a process this user may not inspect is skipped, not guessed at."""
    if not os.path.isdir("/proc"):
        return None
    me = os.getpid()
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        try:
            pid = int(fd_dir.split("/")[2])
            if pid == me:
                continue
            for fd in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, fd)) == node:
                    with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
                        return (f.read().strip() or "?"), pid
        except (OSError, ValueError):
            continue
    return None


def _usb_cause(source):
    node = _usb_node(source)
    if not node or not os.path.isdir("/dev"):
        return ""
    if not os.path.exists(node):
        return f"no device at {node} - not plugged in"
    if not os.access(node, os.R_OK | os.W_OK):
        return f"permission denied on {node}"
    other = _device_holder(node)
    if other:
        return f"in use by another program ({other[0]}, pid {other[1]})"
    return ""


def _probe_connect(host, port, limit):
    """A socket connected to host:port by `limit`, or None when the name
    lookup itself has not finished by then. Raises as create_connection does
    (gaierror, ConnectionRefusedError, OSError), with the first failure.

    Not create_connection: its timeout bounds each connect but not the name
    lookup (a dead DNS server holds getaddrinfo for seconds a try), and it
    gives every address of a multi-address name a full timeout of its own.
    The lookup runs on a daemon thread here, and the addresses share what is
    left of the one deadline."""
    import socket                                           # noqa: PLC0415
    found = []

    def lookup():
        try:
            found.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as e:
            found.append(e)
    worker = threading.Thread(target=lookup, name="camera-probe-dns",
                              daemon=True)
    worker.start()
    worker.join(max(0.0, limit - time.monotonic()))
    if not found:
        return None
    if isinstance(found[0], Exception):
        raise found[0]
    first = None
    for family, kind, proto, _name, addr in found[0]:
        left = limit - time.monotonic()
        if left <= 0:
            break
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(left)
            sock.connect(addr)
            return sock
        except OSError as e:
            sock.close()
            first = first or e
    raise first or TimeoutError(f"no address of {host} answered in time")


def _probe_ask(sock, method, target, proto, host, cseq, auth=None,
               deadline=None):
    """One RTSP/HTTP request on an open socket: (status, [WWW-Authenticate]).
    `deadline` (monotonic) caps the answer further, for the whole probe."""
    lines = [f"{method} {target} {proto}", f"CSeq: {cseq}", f"Host: {host}",
             "Accept: application/sdp", "User-Agent: iriv-camera-check"]
    if auth:
        lines.append(f"Authorization: {auth}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    buf = b""
    # One deadline for the whole answer, not per recv(): a device that
    # trickles a byte a second and never ends its header (something that is
    # not a camera, on the camera's port) otherwise holds the reconnect
    # ladder -- and every camera of this system with it -- for hours.
    limit = time.monotonic() + _PROBE_TIMEOUT_S
    if deadline is not None:
        limit = min(limit, deadline)
    while b"\r\n\r\n" not in buf and len(buf) < 16384:
        left = limit - time.monotonic()
        if left <= 0:
            raise TimeoutError("no complete answer in time")
        sock.settimeout(left)
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    m = re.match(r"(?:RTSP|HTTP)/\d(?:\.\d)?\s+(\d{3})", head[0] if head else "")
    challenges = [v.split(":", 1)[1].strip() for v in head[1:]
                  if v.lower().startswith("www-authenticate:")]
    return (int(m.group(1)) if m else 0), challenges


def _probe_auth(challenges, user, password, method, target):
    """The Authorization header for the camera's challenge -- Digest when it
    offers it (nearly every IP camera), Basic otherwise."""
    import base64                                           # noqa: PLC0415
    import hashlib                                          # noqa: PLC0415
    digest = next((c for c in challenges if c.lower().startswith("digest")), "")
    if not digest:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {token}"
    p = dict(re.findall(r'(\w+)="?([^",]*)"?', digest[6:]))

    def h(text):
        return hashlib.md5(text.encode()).hexdigest()       # noqa: S324 -- RTSP Digest IS md5
    ha1 = h(f"{user}:{p.get('realm', '')}:{password}")
    ha2 = h(f"{method}:{target}")
    out = (f'Digest username="{user}", realm="{p.get("realm", "")}", '
           f'nonce="{p.get("nonce", "")}", uri="{target}"')
    if "auth" in str(p.get("qop", "")).split(","):
        cnonce = os.urandom(8).hex()
        resp = h(f"{ha1}:{p.get('nonce', '')}:00000001:{cnonce}:auth:{ha2}")
        out += f', qop=auth, nc=00000001, cnonce="{cnonce}"'
    else:
        resp = h(f"{ha1}:{p.get('nonce', '')}:{ha2}")
    out += f', response="{resp}"'
    if p.get("opaque"):
        out += f', opaque="{p["opaque"]}"'
    return out


def _net_cause(source):
    """What an IP camera says when asked directly. RTSP is asked DESCRIBE (the
    request that names the stream path, so a wrong path answers 404) and HTTP
    is asked GET; a 401 is retried once with the URL's own credentials, so
    "needs a password" and "rejected this password" are told apart."""
    import socket                                           # noqa: PLC0415
    import urllib.parse                                     # noqa: PLC0415
    try:
        parts = urllib.parse.urlsplit(str(source).strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    port = port or {"rtsp": 554, "rtsps": 322, "http": 80,
                    "https": 443}.get(scheme)
    if not host or not port:
        return ""
    # The whole probe -- lookup, connect and both requests -- inside twice
    # the probe limit; the lookup and connect inside the first half of it.
    start = time.monotonic()
    deadline = start + 2 * _PROBE_TIMEOUT_S
    try:
        sock = _probe_connect(host, port, start + _PROBE_TIMEOUT_S)
        if sock is None:
            return ""                   # the lookup itself did not finish
    except socket.gaierror:
        return f"host {host} not found"
    except ConnectionRefusedError:
        return f"{host}:{port} refused the connection"
    except OSError:
        return f"no answer from {host}:{port}"
    user = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    with sock:
        if scheme not in ("rtsp", "http"):
            return ""                   # TLS: reachable is all that is known
        sock.settimeout(_PROBE_TIMEOUT_S)
        hostport = f"{host}:{port}"
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if scheme == "rtsp":
            method, proto, target = "DESCRIBE", "RTSP/1.0", f"rtsp://{hostport}{path}"
        else:
            method, proto, target = "GET", "HTTP/1.1", path
        try:
            code, challenges = _probe_ask(sock, method, target, proto,
                                          hostport, 1, deadline=deadline)
            if code == 401 and user:
                code, _ = _probe_ask(sock, method, target, proto, hostport, 2,
                                     _probe_auth(challenges, user, password,
                                                 method, target),
                                     deadline=deadline)
        except OSError:
            return ""
    label = scheme.upper()
    if code == 401:
        return (f"username or password rejected ({label} 401)" if user
                else f"needs a username and password ({label} 401)")
    if code == 403:
        return f"access refused for this user ({label} 403)"
    if code == 404:
        return f"stream path not found ({label} 404)"
    if 200 <= code < 300:
        return f"answered, but its video could not be played ({label} {code})"
    if code:
        return f"answered with status {code} ({label})"
    return ""


def diagnose_open(source):
    """A short fact saying why `source` did not open, or "" when it cannot be
    told. Never raises: a diagnosis that fails is a camera with a vaguer
    message, never a camera that crashes the recovery ladder."""
    key = str(source or "").strip()
    now = time.time()
    hit = _diag_cache.get(key)
    if hit and now - hit[0] < (_REJECTED_TTL_S if hit[1].startswith(_REJECTED)
                               else _DIAG_TTL_S):
        return hit[1]
    try:
        cause = _net_cause(key) if "://" in key else _usb_cause(key)
    except Exception:                                       # noqa: BLE001
        cause = ""
    if len(_diag_cache) > 64:
        # Room is made from the cheap answers; a rejection is what keeps the
        # camera's account from locking, so it goes last.
        for old in [k for k, v in _diag_cache.items()
                    if not v[1].startswith(_REJECTED)]:
            del _diag_cache[old]
        if len(_diag_cache) > 64:
            _diag_cache.clear()
    _diag_cache[key] = (now, cause)
    return cause


class Camera:
    def __init__(self, config):
        self.config = dict(config or {})
        # NO default of "synthetic": an unconfigured camera must read as
        # "nothing chosen yet", not quietly run a fake picture. Only an
        # explicit source="synthetic" (a deliberate dev/test choice) is ever
        # treated as one -- see the `synthetic` property below.
        self.source = self.config.get("source", "")
        self.width = int(self.config.get("width", 640))
        self.height = int(self.config.get("height", 480))
        self.fps = float(self.config.get("fps", 10)) or 10.0
        # What the DEVICE is asked for at open, held apart from self.fps: the
        # chip balancer rewrites self.fps down to the inference allowance,
        # and a reopen that recycled the rewritten value negotiated the
        # camera itself at 15fps -- then every consumer of the shared feed
        # was stuck there too, and the balancer's own ceiling (measured
        # delivery) locked the whole box at the low rate for good.
        self.device_fps = float(self.config.get("device_fps", 0) or 0)
        #: What the device actually agreed to, once negotiated.
        self.device_rate = 0.0
        self._cap = None
        self._lock = threading.Lock()
        self._last_jpeg = None
        self._tick = 0
        self._registered = None       # device source registered for sharing
        self.error = None
        self._latest = None           # newest frame from the grab thread
        self._grab_seq = 0            # bumped per kept grab; last_jpeg tracks it
        self.held_still = False       # a picture file: one frame, held (see _grab_loop)
        self.asleep = False           # closed between snaps on purpose (see sleep)
        self.low_rate = False         # kept open but read once a second (see snapcam)
        self._jpeg_seq = -1           # _grab_seq the cached jpeg was encoded at
        self._latest_full = None      # its pre-downscale original (or None)
        self._last_full = None        # the full frame matching the last read()
        # Raw-MJPEG mode: the capture hands over the compressed buffer and the
        # grab loop decodes it at 1/_reduce in the DCT domain (see
        # _dct_reduction). _latest_raw / _last_raw keep the bytes so
        # last_full() can still produce the full-size original on demand.
        self._raw_jpeg = False
        self._reduce = 1
        self._latest_raw = None
        self._last_raw = None
        self._grab_stop = None
        self._grab_thread = None
        #: The depth camera's capture, when this camera IS one. Set in open();
        #: named here so depth_frame() answers on a camera that never opened,
        #: and on the synthetic one, without a hasattr dance.
        self._orbbec = None
        self._read_fail = 0
        self._last_good = time.time()
        self._last_reopen = 0.0
        # How long a silence has to last before the stream is reopened. Set
        # per camera once the format is known (_apply_capture_settings): a big
        # uncompressed one gets a short bar, because its stalls clear on a
        # reopen and the wait IS the frozen picture.
        self._stall_s = _REOPEN_AFTER_STALL_S
        self._weak_since = 0.0    # when delivery first went bad
        self._frame_mark = None   # fingerprint of the last kept frame
        self._frozen_since = 0.0  # when the picture stopped changing
        self._consume_fails = 0   # stalled reconnects to a shared feed
        self._consuming = None        # /raw URL when sharing another app's decode
        self.measured_fps = 0.0       # real delivery rate, counted in the grab loop
        self._fps_count = 0
        self._fps_t0 = 0.0
        #: A file's own frame rate, once opened. 0 for a device, whose rate is
        #: whatever it happens to deliver.
        self.native_fps = 0.0

    # -- lifecycle ----------------------------------------------------------

    @property
    def synthetic(self):
        # An UNSET source is never synthetic, even on a box with no cv2 at
        # all: this property used to answer True for "no source configured"
        # the moment cv2 was missing, so open() rendered a placeholder for a
        # camera nobody had chosen yet -- the exact "quietly runs a fake
        # picture" behaviour this app was fixed to stop doing (see
        # tests/smoke_nocamera.py). No source means no source, backend or not.
        if not str(self.source).strip():
            return False
        return str(self.source) == "synthetic" or cv2 is None

    @property
    def running(self):
        return self.synthetic or self._cap is not None

    @property
    def label(self):
        """The camera as a person reads it, PASSWORD MASKED: it goes into
        state.json (`camera`, `cameras[].label`), event details and log
        lines, and from state.json to every signed-in account (Pipi H2). The
        real address is `source`; nothing that opens a camera reads this."""
        return "synthetic" if self.synthetic else faults.mask_label(str(self.source))

    @property
    def file_path(self):
        """Absolute path of a `file:` source, or None for a real device.

        Stored RELATIVE to the system's media directory ("file:clip.mp4")
        rather than absolute, so a system exported as a template still points
        at its own footage on the next box. An absolute path is honoured too,
        for a file that lives outside the system.
        """
        s = str(self.source).strip()
        if not s.startswith("file:"):
            return None
        rel = s[5:].strip()
        if not rel:
            return None
        if os.path.isabs(rel):
            return rel
        base = str(self.config.get("media_dir") or "").strip()
        return os.path.join(base, rel) if base else rel


    def _negotiate_fps(self, cap):
        """Ask the device for the best rate it can actually do in this mode.

        `device_fps` in config is taken at its word. With nothing configured
        the camera is ASKED rather than assumed: set an impossibly high rate
        and read back what the driver clamped it to, which is how V4L2 reports
        the fastest interval for the format and size just negotiated. No new
        dependency, and it works on any backend that implements CAP_PROP_FPS
        -- one that does not simply leaves its default, exactly as before.

        Why the highest rather than the pipeline's own rate: the two are
        different questions. `fps` is how many frames this system INFERENCES;
        the device rate decides how FRESH the frame it picks up is. Asking the
        camera for 10 when it can do 60 means every frame handed to detection
        can be up to 100ms stale, and the chip balancer makes that worse -- it
        rewrites `fps` down when several systems share the accelerator, and
        the camera would follow it down, for every consumer of the shared feed
        too.

        Capped: past _MAX_DEVICE_FPS the bus and the decoder pay for frames
        nothing looks at. Floored at the pipeline rate, since asking for fewer
        frames than are consumed starves it.
        """
        want = float(self.device_fps or 0)
        if want <= 0:
            try:
                cap.set(cv2.CAP_PROP_FPS, _FPS_PROBE)
                got = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            except Exception:                               # noqa: BLE001
                got = 0.0
            # A driver that answers 0, or parrots the probe back without
            # meaning it, has told us nothing -- keep the configured rate.
            want = got if 1.0 <= got < _FPS_PROBE else float(self.fps or 10)
        want = max(float(self.fps or 1), min(_MAX_DEVICE_FPS, want))
        try:
            cap.set(cv2.CAP_PROP_FPS, want)
        except Exception:                                   # noqa: BLE001
            return
        try:
            self.device_rate = float(cap.get(cv2.CAP_PROP_FPS) or 0) or want
        except Exception:                                   # noqa: BLE001
            self.device_rate = want
        # Said out loud once per open. "Why is the picture 200ms behind" is
        # answered by the rate the DEVICE agreed to, and that number was
        # previously nowhere -- not in the log, not on the page.
        log.info("camera %r: device rate %.0f fps%s", self.source,
                 self.device_rate,
                 "" if self.device_fps else " (highest it offers)")

    def _apply_capture_settings(self, cap, backend):
        """Negotiate format, size, rate and queue depth on a freshly
        opened capture.

        Its own method so a REOPEN after a stall gets the identical
        negotiation -- a camera that came back at the sensor default
        (2592x1944 YUYV, half a frame a second) would look like the same
        freeze it was recovering from.
        """
        if backend != cv2.CAP_GSTREAMER and self._consuming is None:
            # MJPG FIRST, then the size. OpenCV's V4L2 default is uncompressed
            # YUYV, and a webcam's YUYV modes are bus-limited: 1280x960 YUYV
            # at 30fps is ~590 Mbit/s -- more than an entire USB2 bus, before
            # the touchscreen and the serial adapter sharing it say a word.
            # The same camera offers MJPG at every size at 30fps for exactly
            # this reason. Measured here: YUYV negotiated -> 0.5 fps delivered;
            # the picture "freezes" while the bus retries. Order matters --
            # some drivers lock the format on the first size request.
            #
            # A file is decoded, not negotiated, so this applies to DEVICES
            # only; a backend that ignores FOURCC (or a camera with no MJPG)
            # simply keeps its default, which is what the try is for.
            if not self.file_path:
                try:
                    cap.set(cv2.CAP_PROP_FOURCC,
                            cv2.VideoWriter_fourcc(*"MJPG"))
                except Exception:       # noqa: BLE001
                    pass
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            if not self.file_path:
                # Ask the DEVICE for a rate. Without this a camera keeps its
                # mode's default -- full resolution runs at its slowest table
                # entry -- and the box then decodes frames it is about to
                # throw away.
                #
                # Asked AFTER the size is settled, below, because the fastest
                # rate a camera offers depends on the mode it is in: the same
                # sensor does 60fps at 640x480 and 5fps at its full frame.
                pass
                # Setting FOURCC makes some drivers re-negotiate the size to
                # the new format's DEFAULT -- usually the sensor's maximum
                # (measured here: a 640x480 ask answered 2592x1944). Read
                # back and re-assert once; if the driver still refuses, the
                # downscale in the grab loop keeps detection right and only
                # the decode cost is lost.
                try:
                    got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    if got_w and abs(got_w - int(self.width)) > 16:
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                except Exception:       # noqa: BLE001
                    pass
            if not self.file_path:
                self._negotiate_fps(cap)
            # How many frames the DRIVER may hold. One is the lowest latency
            # and the right answer for a compressed stream -- the grab thread
            # drains continuously, so the queue is never the freshness limit.
            #
            # It is the WRONG answer for a big uncompressed camera. Measured
            # on an Arducam B0499, whose only mode is YUYV 1456x1088 at 44fps
            # (3.1 MB a frame, 139 MB/s): standalone it ran 40 seconds at 43.4
            # fps with a 23ms worst gap, and under the app it froze for ten
            # seconds at a time, every time. With one buffer the driver has
            # nowhere to put the next frame the moment the grab thread is late
            # -- and a Python thread sharing a box with inference, annotation
            # and JPEG encoding IS late sometimes. The stream then stalls
            # (select() timeout) and only a reopen brings it back.
            #
            # A few buffers absorb exactly that. They cost no latency here:
            # the grab loop takes the NEWEST frame and drops the rest.
            frame_bytes = int(self.width) * int(self.height) * 2
            big = frame_bytes >= _BIG_FRAME_BYTES
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 8 if big else 1)
            except Exception:           # noqa: BLE001 -- not every backend has it
                pass
            # ...and when one of these DOES stall, get it back quickly. The
            # stall itself is cheap to recover from -- a reopen is about a
            # second -- but the wait before trying is what an operator sees as
            # a frozen picture. On the camera above the driver blocks in
            # select() for five seconds before even reporting the failure, so
            # the default eight-second bar turned every hiccup into a
            # ten-second freeze.
            self._stall_s = _BIG_STALL_S if big else _REOPEN_AFTER_STALL_S
            self._choose_raw_decode(cap, backend)

    def _choose_raw_decode(self, cap, backend):
        """Decode a big MJPEG device at the pipeline's size, not the sensor's.

        A 5MP USB camera costs a full JPEG decode plus a 5MP -> 640 resize per
        kept frame -- 43 ms on a CM5, at ten or more frames a second, on a
        thread that then holds the box's busiest core (measured: 60% of the
        app's samples, against 5% for inference). libjpeg can reconstruct a
        1/2, 1/4 or 1/8 frame straight from the DCT coefficients for a
        fraction of that, but VideoCapture never offers it -- so the capture
        is asked for the compressed buffer as-is and the grab loop decodes it
        at the right reduction itself. Same 640-wide frame out; one 8 ms call
        instead of two totalling 43.

        V4L2 only, MJPG only (read back, not assumed), and only when the
        negotiated frame is at least twice max_side -- anything smaller is not
        worth the detour. Re-decided on every (re)open, because a reopen can
        land on a different format.
        """
        self._raw_jpeg, self._reduce = False, 1
        if cv2 is None or backend != getattr(cv2, "CAP_V4L2", object()):
            return
        if self.file_path or self._consuming is not None:
            return
        if _fourcc_name(cap) != "MJPG":
            return
        try:
            got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        except Exception:                                   # noqa: BLE001
            return
        limit = int(self.config.get("max_side") or self._MAX_SIDE)
        reduce = _dct_reduction(max(got_w, got_h), limit)
        if reduce == 1 or _reduced_flag(reduce) is None:
            return
        try:
            if not cap.set(cv2.CAP_PROP_CONVERT_RGB, 0):
                return
        except Exception:                                   # noqa: BLE001
            return
        self._raw_jpeg, self._reduce = True, reduce
        log.info("camera %r: %dx%d MJPG decoded at 1/%d (%dx%d) in the DCT "
                 "domain -- one call in place of a full decode and a resize",
                 self.source, got_w, got_h, reduce,
                 got_w // reduce, got_h // reduce)

    def _leave_raw_decode(self, cap, why):
        """Back to the driver's own conversion, on the spot. The worst case of
        this feature must be the cost it replaced, never a camera that reads
        as failed because one buffer would not decode."""
        self._raw_jpeg, self._reduce = False, 1
        self._latest_raw = None
        try:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        except Exception:                                   # noqa: BLE001
            pass
        log.info("camera %r: raw MJPEG decode off (%s); the driver converts "
                 "frames again", self.source, why)

    @property
    def gige_binning(self):
        """Sensor binning for a GigE camera: 1 (off), 2 or 4. These are the
        modes a sensor implements; anything else is asking for nothing."""
        try:
            bins = int(self.config.get("binning") or 1)
        except (TypeError, ValueError):
            bins = 1
        return bins if bins in (2, 4) else 1

    def _gige_tuning(self):
        """The exposure and gain properties for an `aravissrc`, as pipeline text.

        Both are ALWAYS stated. aravissrc leaves them "off" -- meaning whatever
        the camera was last set to, by whatever software touched it last --
        which is how one camera gives a black picture on one board and a
        blown-out one on the next, with nothing on either box to explain it.
        Continuous is the default here: a camera that adapts to the light is
        what a person expects a camera to do.

        Mirrors core/services/detector_control.gst_gige_pipeline -- duplicated
        on purpose (the app must not import core/), so both change together.
        """
        def mode(key):
            v = str(self.config.get(key) or "").strip().lower()
            if v in ("continuous", "auto", "true", "1"):
                return "on"
            if v in ("manual", "false", "0"):
                return "off"
            return v if v in ("on", "once", "off") else "on"

        def number(key):
            try:
                return float(self.config.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        exposure, gain = mode("exposure_auto"), mode("gain_auto")
        out = f" exposure-auto={exposure} gain-auto={gain}"
        # Sensor binning, the one setting that buys frame rate on a link that
        # cannot carry the full picture: 2x2 sends a quarter of the data and
        # keeps the WHOLE field of view (a smaller size CROPS a GigE camera --
        # it sets the sensor's region of interest, it does not scale).
        if self.gige_binning > 1:
            out += f" h-binning={self.gige_binning} v-binning={self.gige_binning}"
        # A manual value belongs to manual mode only: set while the camera is
        # measuring for itself, it is overwritten on the next frame.
        if exposure == "off" and number("exposure_us") > 0:
            out += f" exposure={number('exposure_us'):.0f}"
        if gain == "off" and number("gain_db") > 0:
            out += f" gain={number('gain_db'):.2f}"
        return out

    def _capture_spec(self, sized=True):
        """Resolve self.source to (cv2 arg, backend flag). Supports every camera
        connection a deployment might use, each opened the way it actually works:

          "gige[:name]"      -> GigE Vision camera via an Aravis GStreamer pipeline
          "gst:<pipeline>"    -> a raw GStreamer pipeline (escape hatch)
          "rtsp/http://…"     -> network stream via FFmpeg (H.264/H.265/MJPEG)
          "0", "1", …         -> USB/UVC via the V4L2 device index

        CSI ("libcamera[:name]") is refused: this hardware has no ribbon
        connector. Returns None so open() reports it as a camera error the page
        can show, rather than raising up through the detection loop. Mirrors
        detector_control.open_capture_spec -- duplicated on purpose (the app
        must not import core/), so both must change together.
        """
        s = str(self.source).strip()
        w, h, f = self.width, self.height, int(self.fps)
        if not s:
            return None
        if s == "libcamera" or s.startswith("libcamera:"):
            return None
        # A recorded file, played as if it were a camera. Same FFmpeg backend
        # a network stream uses -- what makes a file different is not how it
        # opens but that it ENDS, which the grab loop handles by rewinding.
        path = self.file_path
        if path:
            return path, cv2.CAP_FFMPEG
        if s == "gige" or s.startswith("gige:"):
            name = s.split(":", 1)[1] if ":" in s else None
            # Inside double quotes in pipeline TEXT. A quote, backslash or
            # "!" in the name would end the property and append elements of
            # its own; the master refuses such a name on save
            # (detector_control.gige_name_ok), and a config that has one
            # anyway opens nothing rather than a pipeline nobody wrote.
            if name and not re.match(r"^[\w .:()+/-]{1,128}$", name):
                return None
            cam = f' camera-name="{name}"' if name else ""
            # `sized` off drops the size caps and takes whatever the camera's
            # own sensor gives. Asking a GigE camera for a size it cannot
            # reach is not a smaller picture: the caps never negotiate, the
            # stream stops, and the app reports "gave no frames" of a camera
            # that is plugged in, powered and answering. open() retries this
            # way, so a size inherited from the camera this one replaced
            # costs a downscale instead of the whole feed.
            # A binned sensor sends native/N, so a size written for the full
            # sensor describes nothing it can send: the caps are dropped and
            # the frame is scaled downstream, exactly as in the retry above.
            caps = (f" ! video/x-raw,width={w},height={h}"
                    if sized and self.gige_binning == 1 else "")
            return (f"aravissrc{cam}{self._gige_tuning()}{caps}"
                    " ! videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1",
                    cv2.CAP_GSTREAMER)
        if s.startswith("gst:"):
            return s[4:], cv2.CAP_GSTREAMER
        if "://" in s:
            return s, cv2.CAP_FFMPEG
        try:
            # The backend is PINNED to V4L2 on Linux, never auto-picked: an
            # OpenCV built with GStreamer opens a bare index through a gst
            # pipeline, and every property set after it -- MJPG, size, fps --
            # is an "unhandled property" silently dropped. That is how a
            # camera asked for 640x480 MJPG ran YUYV 1280x960: the asks never
            # reached the driver at all.
            index = int(s)
            v4l2 = getattr(cv2, "CAP_V4L2", None)
            # /dev/cam-usb<n> is a udev symlink pinned to the PHYSICAL USB
            # socket, so "camera 0" means "whatever is plugged into socket 0"
            # and follows the device to whichever /dev/video node it
            # enumerated as. Resolved to the node's INTEGER index: this
            # OpenCV build's V4L2 backend refuses a "/dev/videoN" path
            # ("can't be used to capture by name" -- observed, not
            # theoretical). Without the symlink (dev laptop, rule not
            # installed) the bare kernel index still works as before.
            link = f"/dev/cam-usb{index}"
            if v4l2 and os.path.islink(link):
                real = os.path.realpath(link)
                m = re.match(r"/dev/video(\d+)$", real)
                if m and os.path.exists(real):
                    return int(m.group(1)), v4l2
            return index, (v4l2 if v4l2 and os.path.exists(f"/dev/video{index}")
                           else None)
        except (TypeError, ValueError):
            return s, cv2.CAP_FFMPEG

    def _failed(self, event_id, message):
        """Record why this camera did not open, once, where the reason is known.

        The caller only ever saw False and a string on the object, so "camera
        not found" and "another system holds it" arrived at the page as the
        same silence. Filed HERE because this is the only place that can tell
        the two apart, and one record per failure is what the page counts.
        """
        # Redacted HERE, once, because this string is published: it rides the
        # heartbeat to the camera card on every dashboard, and an RTSP source
        # carries its password in the URL.
        self.error = message = faults.redact(message)
        log.error("camera %s: %s", faults.redact(repr(self.source)), message,
                  extra=faults.event(event_id))
        return False

    def open(self):
        if self.synthetic:
            if Image is None:
                return self._failed(
                    faults.CAM_NOT_FOUND,
                    "no camera backend: install opencv-python or pillow")
            return self.error is None
        # Another app on this box already decodes this EXACT source? Consume
        # its downscaled /raw MJPEG over loopback instead of opening a second
        # session: one expensive H.264/V4L2 decode per camera, not per app.
        # (Applies to network streams too -- three apps each decoding the same
        # 1080p RTSP stream is pure wasted CPU.)
        # [camera] share = false opts this system out: it always decodes its
        # own stream (lowest latency), at the cost of a second decode session.
        self._consuming = None
        # The depth camera opens through its own SDK, not through cv2, so it
        # takes this branch and skips everything below that is about V4L2
        # modes and shared MJPEG feeds. It is never shared: the SDK holds the
        # USB device outright, and a second opener gets nothing.
        self._orbbec = None
        if orbbec.is_orbbec_source(self.source):
            cap = orbbec.OrbbecCapture(self.source, self.width, self.height,
                                       self.fps)
            if not cap.isOpened():
                return self._failed(
                    faults.CAM_NOT_FOUND,
                    cap.error or "the depth camera did not open")
            self._orbbec = cap
            self._cap = cap
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height)
            native = cap.get(cv2.CAP_PROP_FPS)
            if native:
                self.native_fps = float(native)
            log.info("[CAM] %r: Orbbec depth camera, %sx%s with aligned depth",
                     self.source, self.width, self.height)
            self._start_grabbing()
            return True
        shared = (shared_feed_source(str(self.source))
                  if self.config.get("share", True) is not False else None)
        if shared:
            cap = cv2.VideoCapture(shared, cv2.CAP_FFMPEG)
            if cap.isOpened():
                self._consuming = shared
                backend = cv2.CAP_FFMPEG
                log.info("camera %r: consuming shared feed %s",
                         self.source, shared)
            else:
                cap.release()
                shared = None
        if not shared:
            spec = self._capture_spec()
            if spec is None:
                # Two different "cannot open", both the operator's to fix, so
                # each says which one it is rather than sharing one message.
                return self._failed(
                    faults.CAM_NOT_FOUND,
                    "no camera chosen - pick one on the Camera Setup page"
                    if not str(self.source).strip() else
                    "CSI (libcamera) cameras are not supported on "
                    "this hardware - re-add this camera as USB, "
                    "IP (RTSP) or GigE")
            arg, backend = spec
            cap = cv2.VideoCapture(arg, backend) if backend is not None else cv2.VideoCapture(arg)
            # A GigE camera that will not open AT THE ASKED SIZE opens at its
            # own. The asked size is a request; the camera's sensor is a fact,
            # and one is not worth losing the feed over -- especially since
            # the usual reason is a size inherited from the camera this one
            # replaced (a 2592x1944 USB webcam, on a sensor that stops at
            # 2448x2048). The frame is downscaled downstream as always.
            if not cap.isOpened() and str(self.source).strip().startswith("gige"):
                cap.release()
                arg, backend = self._capture_spec(sized=False)
                log.warning("camera %r: %sx%s was refused - "
                            "opening at the camera's own size",
                            self.source, self.width, self.height)
                cap = cv2.VideoCapture(arg, backend)
        if not cap.isOpened():
            # A device is usually unopenable for ONE reason on this box:
            # another system is already holding it. That is supposed to be
            # invisible -- the second system consumes the owner's /raw feed --
            # so when it is not, the message has to say which system to look
            # at rather than leave "cannot open camera '0'" standing over a
            # camera that is plainly working in the next tab.
            owner = _reg_entry(_reg_load(), self.source) or {}
            if owner.get("pid") and _pid_alive(owner["pid"]):
                return self._failed(
                    faults.CAM_BUSY,
                    f"camera {self.source!r} is held by another "
                    f"system on this box (pid {owner['pid']}) and "
                    "its shared feed did not answer")
            # A network source that will not open is a different job from a
            # missing device: nobody goes looking for an unplugged cable when
            # the camera is three buildings away and the switch is down.
            # ...and each says WHICH of its causes it was, where asking
            # again can tell (diagnose_open above).
            cause = diagnose_open(self.source)
            if "://" in str(self.source):
                return self._failed(
                    faults.CAM_UNREACHABLE,
                    f"camera {self.source!r} did not open: {cause}" if cause
                    else f"camera {self.source!r} did not answer - check the "
                    "address, the credentials and the network path to it")
            if cause.startswith("in use by another program"):
                return self._failed(faults.CAM_BUSY,
                                    f"cannot open camera {self.source!r}: {cause}")
            return self._failed(faults.CAM_NOT_FOUND,
                                f"cannot open camera {self.source!r}"
                                + (f": {cause}" if cause else ""))
        # A GStreamer pipeline already fixes the size in its caps; setting these
        # on a gst capture is ignored at best. A SHARED FEED is skipped too:
        # these properties describe a device to negotiate with, and a consumed
        # /raw MJPEG stream simply IS whatever size its owner serves --
        # forcing FRAME_WIDTH/HEIGHT onto the ffmpeg demuxer made it insert a
        # mismatched swscale that printed "Slice parameters 0, 480 are
        # invalid" once per frame, at frame rate, into every consumer's log.
        # The grab loop already handles a frame size that differs from the
        # configured one.
        self._apply_capture_settings(cap, backend)
        self._cap = cap
        self.error = None
        # A FILE plays at its own rate, and that rate also decides how many of
        # its frames reach detection. The camera's configured fps is a request
        # made of a device -- "give me 10 a second, I cannot use more" -- but
        # footage already IS what it is: asking for 10 from a 25fps clip does
        # not lighten the load, it throws away three frames in five before
        # anything looks at them. The tracker then sees an object jump three
        # times as far between frames, which is exactly how identity breaks on
        # the footage somebody uploaded to test identity.
        #
        # Capped, because a 120fps clip would ask more of the chip than any
        # camera here does. The run loop's balancer still lowers this when the
        # NPU is shared -- a ceiling, not a promise.
        if self.file_path:
            try:
                native = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            except Exception:           # noqa: BLE001
                native = 0.0
            self.native_fps = native if 1.0 <= native <= 120.0 else 0.0
            if self.native_fps:
                self.fps = min(self.native_fps, MAX_FILE_FPS)
        # Drain the device at ITS pace on a thread, keeping only the newest
        # frame. Without this, V4L2/FFmpeg queue frames while the detection
        # loop runs slower than the camera (several models sharing the NPU),
        # and the feed drifts seconds behind real time.
        self._start_grabbing()
        # A size the DEVICE does not offer is the commonest reason a perfectly
        # good camera delivers nothing: many USB sensors expose only a couple
        # of discrete modes (an Arducam B0495 does 1920x1200 and 960x600 and
        # nothing else), and asking for 640x480 makes v4l2 fail the stream
        # outright -- "Internal data stream error", no frames, and the app
        # falls back to synthetic as if the camera were broken or in use.
        #
        # So: if the requested mode produced nothing, drop the request and let
        # the driver pick its own. Whatever it gives back is a real picture of
        # the real scene, which beats a synthetic placeholder every time.
        if self._latest is None and backend != cv2.CAP_GSTREAMER:
            cap.release()
            cap = (cv2.VideoCapture(arg, backend) if backend is not None
                   else cv2.VideoCapture(arg))
            if cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:                           # noqa: BLE001
                    pass
                # The MODE is the driver's choice here, but the RATE within it
                # is still ours to ask for -- and this is the path a camera
                # that ignores size requests actually takes, so leaving it out
                # meant the rate negotiation never ran on exactly the cameras
                # that most need it.
                if not self.file_path:
                    self._negotiate_fps(cap)
                self._cap = cap
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    with self._lock:
                        if self._latest is not None:
                            break
                    time.sleep(0.02)
                with self._lock:
                    got = self._latest
                if got is not None and hasattr(got, "shape"):
                    h, w = got.shape[:2]
                    log.info("[CAM] %r does not offer %sx%s; using the "
                             "device's own %sx%s instead", self.source,
                             self.width, self.height, w, h)
                    self.width, self.height = int(w), int(h)
        # Owning a source we opened DIRECTLY (device index, /dev node, or a
        # network stream): register it so the next app wanting the same source
        # consumes our /raw feed instead of opening its own decode session.
        s = str(self.source)
        if (self._consuming is None and self.config.get("_stream_port")
                and (s.isdigit() or s.startswith("/dev/") or "://" in s)):
            register_camera(s, self.config["_stream_port"],
                            self.config.get("id") or "")
            self._registered = s
        return True

    def _start_grabbing(self):
        """Reset the delivery counters and start the grab thread.

        Every camera ends up here, whatever opened it: a V4L2 index, a network
        stream, a shared MJPEG feed, or the depth camera's SDK. Pulled out of
        open() when the Orbbec branch arrived -- two copies of the state reset
        is exactly how one of them ends up missing a counter.
        """
        self._read_fail = 0
        self._last_good = time.time()
        self._last_reopen = 0.0
        self._weak_since = 0.0    # when delivery first went bad
        self._frame_mark = None   # fingerprint of the last kept frame
        self._frozen_since = 0.0  # when the picture stopped changing
        self._consume_fails = 0   # stalled reconnects to a shared feed
        self._latest = None
        self._latest_full = None
        self._latest_raw = None
        self._grab_stop = threading.Event()
        self._grab_thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._grab_thread.start()
        # Give the first frame a moment to land so the first read() isn't a miss.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with self._lock:
                if self._latest is not None:
                    break
            time.sleep(0.02)

    def depth_frame(self):
        """The latest depth grid and its scale, aligned to THIS camera's
        picture, or (None, 1.0) for a camera that measures no depth.

        Aligned means indexed with the same pixels the detector saw, so a
        detection box can be asked how far away it is without knowing anything
        about lenses. Every other camera answers None, which is what lets the
        rest of the app ask any camera without checking its kind first.
        """
        cap = self._orbbec
        return cap.depth_frame() if cap is not None else (None, 1.0)

    def intrinsics(self):
        """The lens in numbers (fx, fy, cx, cy in pixels), for turning a size
        in pixels into millimetres. None for a camera that cannot measure."""
        cap = self._orbbec
        return cap.intrinsics() if cap is not None else None

    def depth_at(self, box, frame=None):
        """How far away the thing in this detection box is, in millimetres.
        None when this camera measures no depth, or nothing was measured there.

        Takes the box in the DETECTOR's pixels and scales it to the depth grid
        here, because those two are not the same size and nothing at the call
        site would say so. Capture downscales to max_side (640 by default)
        before the model ever sees a frame, while depth stays at the camera's
        own 1280x720 -- so a box handed straight to the depth grid lands on the
        top-left quarter of the picture. Measured on .63: 2399mm for a box that
        is actually 3453mm away, which is not a wrong number that looks wrong.
        """
        grid, scale = self.depth_frame()
        if grid is None or not box:
            return None
        shape = getattr(frame, "shape", None)
        if shape is None:
            with self._lock:
                shape = getattr(self._latest, "shape", None)
        if shape:
            frame_h, frame_w = shape[:2]
            depth_h, depth_w = grid.shape[:2]
            if frame_w and frame_h and (frame_w, frame_h) != (depth_w, depth_h):
                sx, sy = depth_w / float(frame_w), depth_h / float(frame_h)
                box = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
        return orbbec.depth_mm(grid, scale, box)

    def owns_device(self):
        """Is this camera holding a real device that others could share?

        The question the registry answers, asked of the CAMERA rather than of
        a flag set once at open time -- see reaffirm.
        """
        s = str(self.source)
        return bool(self._cap is not None and self._consuming is None
                    and self.config.get("_stream_port")
                    and (s.isdigit() or s.startswith("/dev/") or "://" in s))

    def reaffirm(self):
        """Say so, on heartbeat cadence, whenever this camera owns its device.

        Asked from the CAMERA'S STATE, not from a flag remembered at open
        time. The flag version healed an entry that had been clobbered, and
        nothing else: an owner that never registered in the first place --
        opened down a path that skipped it, or registered before the stream
        port was known -- stayed invisible for the life of the process, and
        every other system on the box then failed with "cannot open camera"
        against a device this one was holding and happily re-serving. Watched
        live on a box running two systems on one USB camera: the owner served
        /raw perfectly, the registry held no entry for it at all, and the
        second system retried the busy device for ever.

        Re-asserting from state costs one read of a small JSON file per
        heartbeat and cannot go stale: if the camera is open and not
        consuming, it IS the owner, whatever anyone remembered.
        """
        if not self.owns_device():
            return
        s = str(self.source)
        entry = _reg_load().get(reg_key(s))
        if not entry or entry.get("pid") != os.getpid():
            register_camera(s, self.config.get("_stream_port"),
                            self.config.get("id") or "")
            self._registered = s

    def close(self):
        if self._grab_stop is not None:
            self._grab_stop.set()
        joined = True
        if self._grab_thread is not None:
            # A wedged V4L2 or GStreamer grab blocks for ~10s, so a 1s join
            # regularly returned with the thread still INSIDE cap.grab() --
            # and the release() below then freed the capture out from under
            # native code that was still using it. That is a segfault, not an
            # exception, and it happened precisely when a camera was frozen:
            # exactly the moment something restarts it.
            self._grab_thread.join(timeout=12.0)
            joined = not self._grab_thread.is_alive()
            self._grab_thread = None
        if self._registered:
            unregister_camera(self._registered)
            self._registered = None
        if self._cap is not None:
            cap, self._cap = self._cap, None
            self._orbbec = None     # the same object; released just below
            if joined:
                try:
                    cap.release()
                except Exception:       # noqa: BLE001 -- shutdown must not raise
                    pass
            else:
                # Dropped rather than released: the grab thread still holds
                # it. Leaking one handle on a camera that is going away beats
                # crashing the whole app to reclaim it.
                log.warning("[CAM] %s: grab thread did not stop - leaving "
                            "the device handle to it", self.source)

    def sleep(self):
        """Release the device between snaps, keeping the last picture.

        A Snapshot camera looks at nothing between triggers, and decoding a
        stream nobody reads is most of what such a camera costs. Unlike
        close() this is deliberate: the camera reads as healthy (stalled_s is
        0, `asleep` says why it has no frames) and wake() opens it again.
        """
        keep = self._latest
        self.close()
        self._latest = keep
        self.asleep = True

    def wake(self):
        """Open the camera again after sleep(). Blocks like open() does."""
        self.asleep = False
        return self.open()

    # -- frames -------------------------------------------------------------

    def read(self):
        """Return one frame, or None.

        NO JPEG IS ENCODED HERE. It used to encode every frame of every camera
        to keep the /raw feed warm -- on the detection loop, whether or not a
        single consumer was connected. last_jpeg() encodes on demand from the
        newest grab, on the HTTP thread that asked (see its docstring), so the
        cache this filled was only ever read by a path that would have built it
        anyway. The measured cost on the box this was found on: one 640px JPEG
        per camera per tick, forever, for nobody.

        The synthetic camera keeps it: it publishes no grab through _latest, so
        on-demand encoding has nothing to work from -- and a mock camera's cost
        is not a cost.
        """
        if self.synthetic:
            frame = self._synthetic_frame()
            self._last_full = None
            if frame is not None:
                jpeg = encode_jpeg(frame)
                if jpeg:
                    with self._lock:
                        self._last_jpeg = jpeg
                        self._jpeg_seq = self._grab_seq
            return frame
        return self._camera_frame()

    # The AI's working size. Frames larger than this cost CPU at EVERY later
    # step (letterbox, annotate, JPEG encode) yet the model still sees only
    # ~640px -- so scale once, at capture. Overridable via [camera].max_side.
    _MAX_SIDE = 640

    def _downscale(self, frame):
        """Cap the longest side at max_side, preserving aspect ratio."""
        try:
            h, w = frame.shape[:2]
        except Exception:               # noqa: BLE001 -- PIL/odd frame: leave it
            return frame
        limit = int(self.config.get("max_side") or self._MAX_SIDE)
        side = max(h, w)
        if limit <= 0 or side <= limit:
            return frame
        scale = limit / side
        return cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)

    def _grab_loop(self):
        """Continuously pull frames so the consumer always gets the NEWEST one.
        Runs at the camera's own rate on a daemon thread; read() just takes the
        latest. Persistent read failures surface as self.error so the main
        loop's camera-recovery logic still works."""
        # grab() consumes a frame cheaply (decode only); retrieve() adds the
        # colorspace convert + copy. The camera may push 25/30fps while the
        # pipeline wants less -- so grab everything (freshness) but only pay
        # for convert + downscale at the pipeline's rate. self.fps is LIVE:
        # the run loop's balancer retunes it as chip share changes.
        last_keep = 0.0
        # A file plays at its OWN rate. A camera pushes frames in real time and
        # grabbing flat out simply keeps the newest; a decoder handed a file
        # answers as fast as the CPU allows, so the same loop would race a
        # 30-second clip past in two and call it 900fps. Paced here rather than
        # by dropping frames later, because every skipped frame is one the
        # tracker never sees -- identity would break on exactly the footage
        # somebody uploaded to test identity.
        playback = self.file_path is not None
        gap = 0.0
        if playback:
            # Resolved in open(), which also raised self.fps to match: a file's
            # own rate is both how fast it plays AND how much of it detection
            # gets to see. A still reports nothing usable, so fall back to the
            # configured rate.
            native = self.native_fps or float(self.fps) or 10.0
            gap = 1.0 / native
        # A single image is not a one-frame video: it cannot be seeked, so the
        # rewind below fails and the read failures would pile up into "cannot
        # read this file" -- an error, on a file that is fine, which the run
        # loop then tries to recover from by reopening for ever. Decoded once
        # and then held instead.
        still = False
        if playback:
            try:
                count = float(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            except Exception:           # noqa: BLE001
                count = 0.0
            still = count <= 1.0
        self.held_still = still
        while not self._grab_stop.is_set():
            cap = self._cap
            if cap is None:
                break
            # The still is already decoded and _latest holds it: there is
            # nothing to grab, and grabbing would only fail.
            if still and self._latest is not None:
                # A held still IS delivering: its one frame is the picture,
                # for as long as the file is the camera. Without this the
                # frame's age grew from the first grab, and after the
                # viewers' stall notice (5s) every one of them was shown
                # "Camera stalled - reconnecting..." over a perfectly good
                # image -- and at 25s their stream was ended.
                self._last_good = time.time()
                time.sleep(gap or 0.1)
                continue
            try:
                ok = cap.grab()
                now = time.time()
                if playback and not ok:
                    # End of the clip: start it again. This is the whole
                    # difference between a file and a camera -- without it the
                    # read failures pile up and the loop reports a broken
                    # camera thirty frames after a perfectly good video ended.
                    # A single image rewinds every frame, which is what makes
                    # it a still "camera" rather than one dead frame.
                    try:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok = cap.grab()
                    except Exception:   # noqa: BLE001
                        ok = False
                    if not ok:
                        # Some containers cannot seek at all, so reopen from
                        # scratch -- the clip still loops, at the cost of one
                        # open per lap. A file that will not reopen either is
                        # genuinely unreadable and falls through to the
                        # failure count below, like any other camera error.
                        spec = self._capture_spec()
                        if spec:
                            arg, backend = spec
                            fresh = (cv2.VideoCapture(arg, backend) if backend
                                     else cv2.VideoCapture(arg))
                            if fresh.isOpened():
                                try:
                                    cap.release()
                                except Exception:   # noqa: BLE001
                                    pass
                                self._cap = cap = fresh
                                ok = cap.grab()
                            else:
                                fresh.release()
                frame = None
                if ok:
                    # Count every grabbed frame: this is the camera's REAL
                    # delivery rate (shown on the HUD), independent of how
                    # many frames the pipeline keeps.
                    if self._fps_t0 == 0.0:
                        self._fps_t0 = now
                    self._fps_count += 1
                    span = now - self._fps_t0
                    if span >= 2.0:
                        self.measured_fps = self._fps_count / span
                        self._fps_count = 0
                        self._fps_t0 = now
                        self._check_collapse(now)
                raw = None
                keep_gap = 1.0 if self.low_rate else 1.0 / max(1.0, float(self.fps))
                if ok and now - last_keep >= keep_gap:
                    ok, frame = cap.retrieve()
                    last_keep = now
                    if ok and self._raw_jpeg and frame is not None:
                        # The compressed buffer, decoded here at 1/_reduce.
                        # A buffer that will not decode -- a driver that did
                        # not honour CONVERT_RGB, a torn frame -- ends raw
                        # mode rather than the camera: the next retrieve is
                        # the plain path this used to be.
                        raw = frame
                        frame = cv2.imdecode(raw, _reduced_flag(self._reduce))
                        if frame is None or getattr(frame, "ndim", 0) != 3:
                            self._leave_raw_decode(cap, "a buffer did not decode")
                            raw, frame = None, None
                            continue
            except Exception:           # noqa: BLE001 -- device yanked mid-read
                ok, frame, raw = False, None, None
            if ok and frame is None:
                # Grabbed but skipped: still paced. The pipeline keeps fewer
                # frames than the file has, and without this the decoder would
                # sprint through the skipped ones -- the clip would be over in
                # seconds while the kept frames arrived at the right rate.
                if playback and gap:
                    time.sleep(max(0.0, gap - (time.time() - now)))
                continue
            if ok and frame is not None:
                small = self._downscale(frame)
                with self._lock:
                    self._latest = small
                    self._grab_seq += 1
                    # Raw mode keeps the BYTES, not a full-size frame: the
                    # original is reconstructed only if last_full() is asked,
                    # which is the whole saving.
                    self._latest_raw = raw
                    # The pre-downscale original, kept ONLY when downscaling
                    # actually happened -- i.e. the decoder genuinely produced
                    # more pixels than the pipeline works at (an RTSP stream
                    # dictates its own size regardless of what was asked; a
                    # USB device honours the ask and there is nothing more).
                    # Decided from the frames themselves, never from the
                    # source string, so any capture backend behaves right.
                    # Costs no decode -- the decode already happened -- and
                    # one held frame of memory (~6 MB at 1080p) per camera.
                    # The measurement path reads it; detection never does.
                    self._latest_full = (frame if small is not frame and raw is None
                                         else None)
                self._read_fail = 0
                self._last_good = time.time()
                self.error = None
                # The raw buffer is the better fingerprint when there is one:
                # a live sensor never emits two byte-identical JPEGs, and the
                # DCT-reduced frame averages away some of the noise the
                # sparse pixel sample relies on.
                self._note_frame(raw if raw is not None else frame)
                # Real-time playback. After the work, so decode time counts
                # towards the frame's budget instead of being added to it.
                if playback and gap:
                    time.sleep(max(0.0, gap - (time.time() - now)))
            else:
                self._read_fail += 1
                if self._read_fail >= 30:
                    self.error = ("cannot read this file" if playback
                                  else "camera read failed")
                    if self._read_fail == 30:
                        # A camera unplugged mid-stream ends up here and
                        # nowhere else: open() succeeded hours ago, so the
                        # only trace was `error` on an object, which the page
                        # shows as a grey card and the log never mentioned.
                        log.error("camera %r: %s after %d reads (last good "
                                  "frame %.0fs ago)", self.source, self.error,
                                  self._read_fail,
                                  time.time() - (self._last_good or 0),
                                  extra=faults.event(faults.CAM_STREAM_STALLED))
                # A LIVE camera that has stopped delivering is reopened. A
                # file already reopens above (end of clip); a camera had no
                # path back at all -- the count climbed for ever, `error` was
                # set, and _latest kept handing out the LAST GOOD FRAME, so
                # the feed showed a still picture while the overlay went on
                # counting fps. Measured on a Hailo-8 box: a USB camera
                # stalled with `select() timeout` every 10s and never came
                # back, not even once the load that stalled it was gone.
                #
                # Paced deliberately: reopening costs a USB re-negotiation, so
                # a camera that is genuinely unplugged must not be hammered.
                # Every ~5s of failure, and the counter is reset by a
                # successful read above.
                stalled = time.time() - (self._last_good or 0)
                bar = getattr(self, "_stall_s", None) or _REOPEN_AFTER_STALL_S
                if (not playback and stalled >= bar
                        and time.time() - self._last_reopen >= _REOPEN_PACE_S):
                    self._last_reopen = time.time()
                    # Belt and braces around the recovery itself. A fault in
                    # here would raise out of the grab thread and kill it --
                    # a stall that could have been recovered becomes a camera
                    # that is dead until the app restarts. That is not a
                    # theoretical worry: an AttributeError in this very call
                    # did exactly that during development.
                    try:
                        self._reopen_live(round(stalled, 1))
                    except Exception as exc:            # noqa: BLE001
                        log.error("camera %r: reopen failed: %s",
                                  self.source, exc, exc_info=True,
                                  extra=faults.event(faults.CAM_STREAM_STALLED))
                time.sleep(0.05)

    def _note_frame(self, frame):
        """Remember whether the kept frame actually changed.

        A sparse sample, not the whole frame: every 64th pixel of every 64th
        row is a few hundred bytes on a 1456x1088 image, cheap enough to hash
        on every kept frame, and more than enough to tell a new frame from a
        repeat -- sensor noise moves those pixels too.
        """
        try:
            mark = hash(frame[::64, ::64].tobytes())
        except Exception:                                   # noqa: BLE001
            return                      # not a numpy frame; nothing to compare
        if mark == self._frame_mark:
            if not self._frozen_since:
                self._frozen_since = time.time()
        else:
            self._frame_mark = mark
            self._frozen_since = 0.0

    def _check_collapse(self, now):
        """Reopen a camera that is still delivering, but barely.

        The stall recovery above asks "has ANY frame arrived recently", and a
        camera limping at 0.5fps answers yes every two seconds -- so it never
        fired on exactly the failure an operator sees as "camera fps: 0.5" on
        the HUD. Degraded is not the same as silent, and it needs its own
        test: delivery far below what the device agreed to, held for long
        enough that it is not a passing hiccup.

        The floor is deliberately generous. A UVC camera in a dim room
        lengthens its exposure and legitimately drops to 7-8fps -- that is the
        camera working, not failing, and reopening it would only make the
        picture worse. _COLLAPSE_FPS is well under any auto-exposure floor, so
        this only fires on a rate no healthy camera produces.
        """
        rate = float(self.device_rate or 0)
        if rate <= 0 or self._consuming is not None:
            return                      # unknown ceiling, or not our device
        # A frozen PICTURE while grabs keep counting. The HUD showed "camera
        # fps: 27.4" over a still image: grab() was succeeding (that is what
        # the number counts) and the stream kept redrawing the overlay, but
        # the frame underneath never advanced. Neither the stall test (frames
        # ARE arriving) nor the rate test (27fps is healthy) can see that, so
        # the frame itself is asked.
        #
        # A live sensor never produces two byte-identical frames -- there is
        # always noise -- so an unchanged fingerprint means the pipeline is
        # handing out a stale buffer, not that the scene is still.
        if self._frozen_since and now - self._frozen_since >= _COLLAPSE_S                 and now - self._last_reopen >= _COLLAPSE_S:
            log.warning("camera %r: same frame for %.0fs at %.1f fps "
                        "- reopening", self.source, now - self._frozen_since,
                        self.measured_fps,
                        extra=faults.event(faults.CAM_STREAM_STALLED))
            self._last_reopen = now
            self._frozen_since = 0.0
            try:
                self._reopen_live(0.0)
            except Exception as exc:                        # noqa: BLE001
                log.error("camera %r: reopen failed: %s", self.source, exc,
                          exc_info=True,
                          extra=faults.event(faults.CAM_STREAM_STALLED))
            return
        # The LOWER of the two bars, not the higher: both have to be
        # breached for this to be a collapse. On a 44fps camera the
        # fraction alone would call 6fps a fault, and 6fps is what a dim
        # room legitimately produces.
        healthy = min(_COLLAPSE_FPS, rate * _COLLAPSE_FRACTION)
        if self.measured_fps >= healthy:
            self._weak_since = 0.0
            return
        if not self._weak_since:
            self._weak_since = now
            return
        if (now - self._weak_since >= _COLLAPSE_S
                and now - self._last_reopen >= _COLLAPSE_S):
            log.warning("camera %r: delivering %.1f fps against %.0f agreed "
                        "- reopening", self.source, self.measured_fps, rate,
                        extra=faults.event(faults.CAM_STREAM_STALLED))
            self._last_reopen = now
            self._weak_since = 0.0
            try:
                self._reopen_live(0.0)
            except Exception as exc:                        # noqa: BLE001
                log.error("camera %r: reopen failed: %s", self.source, exc,
                          exc_info=True,
                          extra=faults.event(faults.CAM_STREAM_STALLED))

    def _reopen_live(self, stalled_s=0.0):
        """Close and reopen a live camera that has stopped delivering frames.

        Best effort and quiet: this runs inside the grab thread, and an
        exception here would take the thread down and turn a recoverable
        stall into a permanently dead camera -- the exact failure it exists
        to end. The old handle is released FIRST: a UVC device will not hand
        out a second streaming handle while the first one holds it, so
        opening before releasing fails every time.
        """
        # A CONSUMER that keeps stalling stops consuming. Two apps can each
        # decide the OTHER owns the camera -- each restarted while the other
        # still held the registry entry -- and then both consume and nobody
        # grabs the device. Seen on a box running two systems on one camera:
        # each log said "consuming shared feed" pointing at the other, the
        # picture froze, and the rate fell to 0.5fps while this recovery
        # reopened the same dead URL 180 times.
        #
        # Reconnecting an upstream that is not producing cannot fix that. So
        # after _CONSUMER_GIVE_UP attempts the shared feed is abandoned and
        # the real device is opened -- whoever gets there first becomes the
        # owner and starts serving, which breaks the loop for both.
        if self._consuming is not None:
            self._consume_fails += 1
            if self._consume_fails >= _CONSUMER_GIVE_UP:
                log.warning("camera %r: shared feed %s is not delivering "
                            "- taking the camera instead", self.source,
                            self._consuming,
                            extra=faults.event(faults.CAM_STREAM_STALLED))
                self._consuming = None
                self._consume_fails = 0
        spec = self._capture_spec()
        if not spec:
            return
        arg, backend = spec
        old, self._cap = self._cap, None
        try:
            old.release()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            fresh = (cv2.VideoCapture(arg, backend) if backend
                     else cv2.VideoCapture(arg))
        except Exception:                                   # noqa: BLE001
            fresh = None
        if fresh is not None and fresh.isOpened():
            self._apply_capture_settings(fresh, backend)
            self._cap = fresh
            # Having just TAKEN the device (the give-up path above), say so:
            # without this the registry still names whoever we were consuming
            # from, so the other app keeps pointing at a feed nobody serves
            # and the deadlock survives the fix that was meant to break it.
            s = str(self.source)
            if (self._consuming is None and self.config.get("_stream_port")
                    and (s.isdigit() or s.startswith("/dev/") or "://" in s)):
                register_camera(s, self.config["_stream_port"],
                                self.config.get("id") or "")
                self._registered = s
            log.info("camera %r: no frame for %ss - reopened",
                     self.source, stalled_s)
        else:
            if fresh is not None:
                try:
                    fresh.release()
                except Exception:                           # noqa: BLE001
                    pass
            self._cap = old                 # keep the handle; try again later

    def latest(self):
        """The newest grabbed frame and its sequence number, or (None, 0).

        For a viewer, not for detection: the stream draws the last known boxes
        onto the FRESHEST picture, at whatever rate the viewer asks for, so
        the video stays smooth while inference runs at its own slower rate.
        Never opens the device and never blocks the grab thread -- it hands
        back the frame the grab loop already stored (see _grab_loop), and the
        sequence number lets a caller skip work it has already done.
        """
        with self._lock:
            return self._latest, self._grab_seq

    def _camera_frame(self):
        if self._cap is None:
            return None
        with self._lock:
            frame = self._latest
            # Snapshotted TOGETHER, under the one lock, so the full frame a
            # caller later asks for is the same instant as the frame returned
            # here -- not whatever the grab thread decoded since.
            self._last_full = self._latest_full
            self._last_raw = self._latest_raw
        if frame is None and self.error is None and self._read_fail >= 30:
            self.error = "camera read failed"
        return frame

    def _synthetic_frame(self):
        """A moving marker on a plain background -- enough to see the stream
        work, cheap enough to run at 10fps on a laptop.

        Returns a numpy BGR array wherever numpy/cv2 exist, i.e. the SAME
        shape of thing a real camera returns. It used to return a PIL Image,
        which the stream could render but the detector could not read
        (`'Image' object has no attribute 'shape'`) -- so the fallback that
        exists to keep a system alive through a camera glitch was instead
        what killed inference: enough failures and the detector dropped to
        `none`, and detection stayed dead after the camera came back. PIL is
        the fallback's fallback, for a box with no numpy at all.
        """
        self._tick += 1
        x = int((self._tick * 7) % max(1, self.width - 80))
        y = int(self.height / 2 + 60 * ((self._tick % 60) / 30.0 - 1))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            import numpy as np                              # noqa: PLC0415
        except ImportError:                                 # pragma: no cover
            np = None
        if cv2 is not None and np is not None:
            try:
                frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                frame[:] = (28, 22, 18)                     # BGR of the old fill
                cv2.rectangle(frame, (x, y), (x + 70, y + 90),
                              (222, 186, 4), 3)             # BGR
                cv2.putText(frame, stamp, (10, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 160, 150), 1)
                return frame
            except Exception:                               # noqa: BLE001
                pass                                        # fall through to PIL
        if Image is None:
            return None
        img = Image.new("RGB", (self.width, self.height), (18, 22, 28))
        d = ImageDraw.Draw(img)
        d.rectangle([x, y, x + 70, y + 90], outline=(4, 186, 222), width=3)
        d.text((10, 10), stamp, fill=(150, 160, 170))
        if np is not None:
            # numpy-without-cv2 (a CI runner, a box mid-reinstall): the frame
            # contract is still "the shape a real camera returns", so convert
            # PIL's RGB to a BGR array. Returning the PIL image here handed
            # the detection loop something with no .shape -- the stream drew
            # fine while inference silently processed nothing, which is
            # exactly the lying-fallback this function's docstring already
            # warns about.
            return np.asarray(img)[:, :, ::-1].copy()
        return img

    def last_full(self):
        """The pre-downscale original of the frame the last read() returned,
        or None when the pipeline produced nothing bigger than what read()
        already gave (a USB device delivering the asked-for size, synthetic,
        a small stream). None is the honest answer there: the small frame IS
        every pixel that exists, and pretending otherwise would be upscaling.
        """
        if self._last_full is None and self._last_raw is not None and cv2 is not None:
            # Raw-MJPEG mode kept the bytes instead of a decoded 5MP frame.
            # Decoded here, once, only because somebody asked -- this is the
            # cost the grab loop stopped paying on every frame.
            try:
                full = cv2.imdecode(self._last_raw, cv2.IMREAD_COLOR)
            except Exception:                               # noqa: BLE001
                full = None
            self._last_full = full
        return self._last_full

    @property
    def stalled_s(self):
        """Seconds since this camera last actually delivered a frame.

        A stalled camera is INVISIBLE at the picture layer: `_latest` and
        `_last_jpeg` keep handing out the last good frame, so a feed of a
        camera that stopped an hour ago looks exactly like a very still room.
        Every viewer path therefore has to be able to ask how old what it is
        about to serve really is; this is that question.
        """
        if self.asleep:
            return 0.0
        gap = time.time() - (self._last_good or 0)
        return gap if gap > 0 else 0.0

    def last_jpeg(self):
        """The most recent encoded frame, for the MJPEG stream. Never opens
        the device -- see the module docstring.

        Encodes a FRESH grab on demand: the cached jpeg is refreshed by
        read(), which the detection loop paces to its chip allowance
        (~10-12fps under sharing) -- while the grab thread receives the
        camera's full 25-30. A consumer system drinking /raw was capped at
        the OWNER'S inference rate by that staleness. The encode runs here,
        on the HTTP thread that asked, so it costs the detection loop
        nothing and costs nobody anything when no one is streaming."""
        with self._lock:
            jpeg = self._last_jpeg
            seq = self._grab_seq
            frame = None if self._jpeg_seq == seq else self._latest
        if frame is not None:
            fresh = encode_jpeg(frame)
            if fresh:
                with self._lock:
                    # Last writer wins; a concurrent read() got the same
                    # frame or a newer one, either of which is correct.
                    if self._jpeg_seq <= seq:
                        self._last_jpeg, self._jpeg_seq = fresh, seq
                return fresh
        return jpeg


def draw_hud(frame, lines):
    """Stamp a status BOX onto the top-right of the annotated stream frame, so
    every viewer -- Detection page, Video Stream, the hub Live Wall -- sees
    what is running and how fast without any extra UI.

    `lines` is a list of (label, value) pairs, one per row. A plain string is
    accepted too (single row, no label). ASCII only -- cv2.putText renders
    anything else as '??'. Returns the frame (drawn in place where possible).
    """
    if frame is None or not lines:
        return frame
    if isinstance(lines, str):
        lines = [("", lines)]
    rows = [f"{label} : {value}" if label else str(value) for label, value in lines]
    row_h, pad = 18, 8

    if cv2 is not None and hasattr(frame, "shape"):
        fw = frame.shape[1]
        box_w = max(12 + 8 * len(r) for r in rows) + pad
        box_h = pad + row_h * len(rows)
        x0 = max(0, fw - box_w)
        cv2.rectangle(frame, (x0, 0), (fw, box_h), (0, 0, 0), -1)
        for i, r in enumerate(rows):
            cv2.putText(frame, r, (x0 + 6, pad + 5 + row_h * i + 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 220, 255), 1, cv2.LINE_AA)
        return frame
    if Image is not None and hasattr(frame, "shape"):
        # A numpy frame with no cv2 to stamp it (the synthetic camera on a
        # box without opencv): draw via PIL, hand back the same array shape
        # the caller gave. `.copy` alone cannot tell an ndarray from a PIL
        # image -- both have it -- which is how this branch got an array and
        # died inside ImageDraw.
        try:
            import numpy as np                              # noqa: PLC0415
        except ImportError:                                 # pragma: no cover
            return frame
        img = Image.fromarray(frame[:, :, ::-1])            # BGR -> RGB
        draw_hud(img, lines)
        return np.asarray(img)[:, :, ::-1].copy()
    if Image is not None and hasattr(frame, "copy"):
        d = ImageDraw.Draw(frame)
        fw = frame.size[0]
        box_w = max(12 + 7 * len(r) for r in rows) + pad
        box_h = pad + row_h * len(rows)
        x0 = max(0, fw - box_w)
        d.rectangle([x0, 0, fw, box_h], fill=(0, 0, 0))
        for i, r in enumerate(rows):
            d.text((x0 + 6, pad // 2 + row_h * i), r, fill=(80, 220, 255))
        return frame
    return frame


def encode_jpeg(frame, quality=80):
    """Encode a cv2 array or a PIL image to JPEG bytes, or None."""
    if frame is None:
        return None
    if cv2 is not None and hasattr(frame, "shape"):
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None
    if Image is not None and hasattr(frame, "shape"):
        # A numpy frame on a box with PIL but no cv2 -- the synthetic camera
        # produces exactly this pairing. Falling through to return None here
        # meant every JPEG consumer (the stream, snapshots) went dark while
        # detection ran fine, the mirror image of the bug where detection
        # died and the stream lived.
        out = io.BytesIO()
        Image.fromarray(frame[:, :, ::-1]).save(out, format="JPEG",
                                                quality=quality)
        return out.getvalue()
    if Image is not None and hasattr(frame, "save"):
        out = io.BytesIO()
        frame.save(out, format="JPEG", quality=quality)
        return out.getvalue()
    return None


_notice_cache = {}


def notice_jpeg(text, width=640, height=480):
    """A black frame carrying one line of text, as JPEG bytes (or None).

    A camera that is still opening has no frame to send. Answering 503 makes
    the browser treat a perfectly healthy camera as broken and start an
    error/retry loop; sending a frame that SAYS what is happening keeps the
    multipart stream open, so the real picture simply starts flowing when the
    device is ready. Cached: it is the same handful of strings forever.
    """
    key = (text, width, height)
    if key in _notice_cache:
        return _notice_cache[key]
    jpeg = None
    if cv2 is not None:
        try:
            import numpy as np                              # noqa: PLC0415

            frame = np.zeros((height, width, 3), dtype=np.uint8)
            size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
            cv2.putText(frame, text, ((width - size[0]) // 2, height // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
            jpeg = encode_jpeg(frame)
        except Exception:                                   # noqa: BLE001
            jpeg = None
    if jpeg is None and Image is not None:
        try:
            img = Image.new("RGB", (width, height), (0, 0, 0))
            ImageDraw.Draw(img).text((20, height // 2), text, fill=(200, 200, 200))
            jpeg = encode_jpeg(img)
        except Exception:                                   # noqa: BLE001
            jpeg = None
    _notice_cache[key] = jpeg
    return jpeg


def _hex_to_bgr(value, default):
    """#rrggbb -> (B, G, R) for cv2, or `default`."""
    value = str(value or "").strip()
    if len(value) == 7 and value[0] == "#":
        try:
            n = int(value[1:], 16)
            return (n & 255, (n >> 8) & 255, n >> 16)
        except ValueError:
            pass
    return default


def usb_capture_indexes():
    """The /dev/video* indexes that are REAL capture nodes, kernel-answered.

    A UVC camera exposes two nodes -- capture and metadata -- and WHICH index
    is which reshuffles on every re-enumeration. A camera on a flaky link
    re-enumerates constantly, so a config that pins "0" points at the
    metadata node half the time, and the app then reopens a node that cannot
    ever produce a frame. This asks each node what it is (V4L2 QUERYCAP, the
    kernel's own answer) so the reconnect ladder can follow the camera to
    wherever it landed.

    A node that answers EBUSY is a capture node in use (metadata nodes are
    never busy), so it is included. Empty on a host without V4L2.
    """
    try:
        import errno                                        # noqa: PLC0415
        import fcntl                                        # noqa: PLC0415
        import struct                                       # noqa: PLC0415
    except ImportError:
        return []
    out = []
    querycap = (2 << 30) | (104 << 16) | (ord("V") << 8)    # VIDIOC_QUERYCAP
    for path in sorted(glob.glob("/dev/video[0-9]*"),
                       key=lambda p: int(re.search(r"(\d+)$", p).group(1))):
        index = int(re.search(r"(\d+)$", path).group(1))
        # USB devices only: the Pi's ISP back-end (pispbe, /dev/video20+)
        # answers QUERYCAP as a capture device, and the ladder probing it
        # wastes a reconnect attempt per node on hardware no camera is
        # behind. The sysfs device path says which bus a node hangs off.
        try:
            real = os.path.realpath(f"/sys/class/video4linux/video{index}/device")
            if "/usb" not in real:
                continue
        except OSError:
            pass                        # no sysfs (not Linux): keep the node
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            if e.errno == errno.EBUSY:
                out.append(index)
            continue
        try:
            buf = bytearray(104)
            fcntl.ioctl(fd, querycap, buf, True)
            caps = struct.unpack("16s32s32sIII3I", bytes(buf))
            dev_caps = caps[5] or caps[4]
            is_capture = dev_caps & 0x00001001              # CAPTURE | MPLANE
            is_m2m = dev_caps & 0x0000C000                  # codec nodes
            if is_capture and not is_m2m:
                out.append(index)
        except OSError:
            pass
        finally:
            os.close(fd)
    return out


def _face_arrow(pts, facing):
    """The IN arrow for a faced line: (tail_px, tip_px), or None.

    It is the DIRECTION OF TRAVEL that counts as an entry: the tail sits out
    on the way-in side and the tip lands on the line, so the arrow reads as
    something crossing INTO the place. Drawn the other way round -- tail on
    the line, tip pointing off into the room -- it read as "this side is in",
    which is a label, not a movement, and an operator has to work out which
    way somebody must walk to be counted.

    The app calls the POSITIVE side of the drawn direction IN (app/zones.py
    `_side_of_line`), and (-dy, dx) is the normal that lands there -- the same
    rule the editor's arrow uses, so the picture and the tally agree.
    """
    if facing not in ("forward", "reverse") or len(pts) != 2:
        return None
    (x1, y1), (x2, y2) = pts[0], pts[1]
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return None
    sign = -1 if facing == "reverse" else 1
    nx, ny = (-dy / length) * sign, (dx / length) * sign
    # A fixed fraction of the line, clamped: an arrow scaled purely off a long
    # line reaches across the frame, and off a short one is invisible.
    reach = max(18.0, min(70.0, length * 0.28))
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    # From the OUT side (-normal) to the line: an object travelling this
    # way is the one counted IN.
    return ((int(mx - nx * reach), int(my - ny * reach)), (int(mx), int(my)))


def _zone_shapes(zone, overlays):
    """Normalize both zone forms into [(points_px, is_line, color_bgr, facing)].

    `zone` is the legacy pixel rectangle [x1,y1,x2,y2]; `overlays` is the modern
    form -- {"points_px", "kind", "violation", "color"} from app/zones.py. Each
    zone draws in its own colour, falling back to red for a violation zone and
    cyan otherwise.
    """
    shapes = []
    for ov in overlays or []:
        # A zone switched off (checkbox, or a workflow Zone Switch) leaves
        # the picture too: a drawn zone that counts nothing reads as armed.
        if ov.get("enabled") is False:
            continue
        pts = ov.get("points_px") or []
        if len(pts) >= 2:
            fallback = (0, 0, 255) if ov.get("violation") else (222, 186, 0)
            color = _hex_to_bgr(ov.get("color"), fallback)
            is_line = ov.get("kind") == "line"
            shapes.append(([tuple(p) for p in pts], is_line, color,
                           str(ov.get("facing") or "forward") if is_line else ""))
    if not shapes and zone and len(zone) == 4:
        x1, y1, x2, y2 = zone
        shapes.append(([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], False,
                       (222, 186, 0), ""))
    return shapes


def _det_rgb(det):
    """(r, g, b) for one detection's box, or None for the default.

    An Alert may say what colour the objects that trip it are drawn in
    (det["box_color"], stamped in run.py). It is NOT conditional on a zone
    violation: a rule with no zone at all -- a class alert, a count over the
    whole frame -- colours its objects the same way.

    Anything that is not a hex colour is ignored rather than guessed at: a
    wrong colour is a wrong answer about which box tripped the rule, and the
    default already says "violation".
    """
    text = str((det or {}).get("box_color") or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


#: The gradient's ends: nearly clear at the top of a zone, a tint at the
#: bottom. Named because two paths draw it now -- _grad_fill and the
#: cached polygon fill -- and they must not drift apart.
_GRAD_LO, _GRAD_HI = 0.05, 0.30


def _grad_fill(out, x1, y1, x2, y2, color, mask=None,
               lo=_GRAD_LO, hi=_GRAD_HI):
    """Blend a vertical gradient of `color` into out[y1:y2, x1:x2].

    WHY a gradient and not a flat tint: a flat wash over a zone hides the
    floor it is drawn on, and an operator watching the feed is reading the
    floor -- who is standing where. Fading from nearly clear at the top to a
    tint at the bottom keeps the shape unmistakably filled (so a zone reads as
    an area, not four lines that happen to meet) while leaving most of the
    picture underneath legible.

    `mask` is a uint8 0/1 array covering the SAME rectangle, for shapes that
    are not rectangles. Clipped to the frame; a degenerate rectangle is
    skipped. Mutates `out` in place.
    """
    import numpy as _np                                    # noqa: PLC0415
    h, w = out.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    region = out[y1:y2, x1:x2]
    rh, rw = region.shape[:2]
    alpha = _np.linspace(lo, hi, rh, dtype=_np.float32).reshape(rh, 1, 1)
    if mask is not None:
        alpha = alpha * mask[:rh, :rw].astype(_np.float32).reshape(rh, rw, 1)
    tint = _np.empty((rh, rw, 3), dtype=_np.float32)
    tint[:] = color
    region[:] = (region.astype(_np.float32) * (1.0 - alpha)
                 + tint * alpha).astype(region.dtype)


#: Built overlays, keyed by the shape that produced them. A zone does not
#: move between frames and neither does its colour, so everything about its
#: gradient except the pixels underneath is the same picture every time.
#:
#: It was ALL being rebuilt per frame per zone: a linspace, a zeroed mask, a
#: fillPoly, a full-size tint array and two float32 conversions of the region.
#: py-spy on a live 3-camera box put 26% of the whole detection loop inside
#: this one function -- the accelerator was idle waiting for the drawing to
#: finish. Cached, a frame does one multiply-add over the zone's bounding box.
_GRAD_CACHE = {}

#: Enough for every zone on every camera at a couple of frame sizes. Past it
#: the cache is dropped whole rather than evicted cleverly: the entries are
#: rebuilt in one frame, and a resolution change invalidates all of them at
#: once anyway.
_GRAD_CACHE_MAX = 64


def _poly_grad(out, pts, color):
    """Gradient-fill a polygon: the mask is built in the shape's own bounding
    box, not the whole frame, so a small zone costs a small allocation -- and
    once, not per frame (see _GRAD_CACHE)."""
    import numpy as _np                                    # noqa: PLC0415
    key = (tuple(map(tuple, pts)), tuple(color), out.shape[1], out.shape[0])
    got = _GRAD_CACHE.get(key)
    if got is None:
        arr = _np.array(pts, dtype=_np.int32)
        x1, y1 = int(arr[:, 0].min()), int(arr[:, 1].min())
        x2, y2 = int(arr[:, 0].max()), int(arr[:, 1].max())
        x1, y1 = max(0, x1), max(0, y1)
        x2 = min(out.shape[1], x2)
        y2 = min(out.shape[0], y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        rh, rw = y2 - y1, x2 - x1
        mask = _np.zeros((rh, rw), dtype=_np.uint8)
        cv2.fillPoly(mask, [arr - _np.array([x1, y1], dtype=_np.int32)], 1)
        # Same gradient _grad_fill draws: nearly clear at the top, a tint at
        # the bottom, so the floor under the zone stays readable.
        alpha = (_np.linspace(_GRAD_LO, _GRAD_HI, rh, dtype=_np.float32)
                 .reshape(rh, 1, 1)
                 * mask.astype(_np.float32).reshape(rh, rw, 1))
        tint = _np.empty((rh, rw, 3), dtype=_np.float32)
        tint[:] = color
        # The weights each side of the blend carries, as OpenCV wants them:
        # one float32 plane per source, same size as the region.
        keep = _np.ascontiguousarray(1.0 - alpha[:, :, 0])
        put = _np.ascontiguousarray(alpha[:, :, 0])
        got = (x1, y1, x2, y2, keep, put, tint.astype(_np.uint8),
               1.0 - alpha, tint * alpha)
        if len(_GRAD_CACHE) >= _GRAD_CACHE_MAX:
            _GRAD_CACHE.clear()
        _GRAD_CACHE[key] = got
    x1, y1, x2, y2, keep, put, tint_u8, keep_f, add_f = got
    region = out[y1:y2, x1:x2]
    # cv2 does this blend in C over every core; numpy does it one float32
    # array at a time. Measured on the same 740x890 zone: 1.2ms against 15ms,
    # per zone per frame, on a loop that also has an accelerator to feed.
    # blendLinear is in every OpenCV this app has run on, but it is an extra
    # module rather than core cv2, so the arithmetic stays here as a fallback
    # -- a missing function must cost frames, not the overlay.
    try:
        region[:] = cv2.blendLinear(region, tint_u8, keep, put)
    except (AttributeError, cv2.error):
        region[:] = (region * keep_f + add_f).astype(region.dtype)


def size_text(det):
    """What a measured detection says under its label, or "" for one that
    carries no measurement. Plain ASCII: Hershey has no diameter sign.

        box     400x250x120 mm
        circle  dia 300 mm
        sides   400 / 250 mm
        depth   2.35 m           (a depth reading with no profile)
    """
    m = det.get("measure") or {}
    shape = m.get("shape")
    if shape == "circle" and m.get("diameter_mm") is not None:
        text = f"dia {m['diameter_mm']:.0f} mm"
    elif shape in ("sides", "line", "polygon") and m.get("sides_mm"):
        text = " / ".join(f"{s:.0f}" for s in m["sides_mm"][:4]) + " mm"
    elif m.get("length_mm") is not None and m.get("width_mm") is not None:
        text = f"{m['length_mm']:.0f}x{m['width_mm']:.0f}"
        if m.get("height_mm") is not None:
            text += f"x{m['height_mm']:.0f}"
        text += " mm"
    elif det.get("depth_mm") is not None:
        return f"{det['depth_mm'] / 1000.0:.2f} m"
    else:
        return ""
    if det.get("depth_mm") is not None and shape != "sides":
        text += f"  {det['depth_mm'] / 1000.0:.2f} m"
    return text


def measure_lines(det):
    """The lines a measured detection is drawn with, in frame pixels.

    Returns (segments, ring, silhouette): `segments` is a list of ((x1, y1), (x2, y2),
    text) -- each measured side with its size, drawn AS the side, so the
    number sits on the edge it belongs to; `ring` is (centre, radius_px,
    text) or None for a round thing. Which lines exist follows the profile:
    the Sides tool gives the pairs that were tapped, Auto gives the outline
    sides that were ticked (all of them if none were), a circle gives its
    ring and the span its two taps measured. One function, so the cv2 and
    PIL painters cannot disagree about where a size is written.
    """
    m = det.get("measure") or {}
    segs = []
    ring = None
    # The extracted silhouette, dense, in frame pixels -- drawn as the
    # object's shape, under the sides.
    silhouette = [(p[0], p[1]) for p in ((m.get("outline") or {}).get("silhouette") or [])]
    mms = m.get("segments_mm") or []
    for i, (a, b) in enumerate(m.get("segments") or []):
        mm = mms[i] if i < len(mms) else None
        segs.append(((a[0], a[1]), (b[0], b[1]), "?" if mm is None else f"{mm:.0f} mm"))
    outline = m.get("outline") or {}
    verts = outline.get("vertices") or []
    if len(verts) >= 3 and not segs:
        sizes = m.get("sides_mm") or []
        for k, i in enumerate(outline.get("sides") or range(len(verts))):
            if not 0 <= i < len(verts):
                continue
            a, b = verts[i], verts[(i + 1) % len(verts)]
            mm = sizes[k] if k < len(sizes) else None
            segs.append(((a[0], a[1]), (b[0], b[1]), "" if mm is None else f"{mm:.0f} mm"))
    if m.get("shape") == "circle" and m.get("centre") and m.get("radius_px"):
        text = (f"r {m['radius_mm']:.0f} mm" if m.get("mode") == "radius" and m.get("radius_mm") is not None
                else f"dia {m['diameter_mm']:.0f} mm" if m.get("diameter_mm") is not None else "")
        ring = ((m["centre"][0], m["centre"][1]), float(m["radius_px"]), text)
        span = m.get("span")
        if span and len(span) == 2:
            segs.append(((span[0][0], span[0][1]), (span[1][0], span[1][1]), ""))
    return segs, ring, silhouette


def _np_pts(points):
    """A polygon for cv2, from (x, y) pairs."""
    import numpy as _np                                     # noqa: PLC0415
    return _np.array(points, dtype=_np.int32).reshape(-1, 1, 2)


def _size_tag(out, text, at, color, scale=0.5):
    """A size written ON a line: black text on a filled tag in the line's
    colour, centred on `at`. Plain text over a picture is unreadable half
    the time -- green on a green carton, white on a bright floor -- and a
    measurement nobody can read is decoration."""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    h, w = out.shape[:2]
    x = int(min(max(2, at[0] - tw // 2), w - tw - 6))
    y = int(min(max(th + 4, at[1] + th // 2), h - base - 2))
    cv2.rectangle(out, (x - 3, y - th - 3), (x + tw + 3, y + base + 2), color, -1)
    cv2.putText(out, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1, cv2.LINE_AA)


def annotate(frame, detections, zone=None, overlays=None, links=None,
             bubbles=None, trails=None):
    """Draw boxes and the zones onto a copy of the frame, for snapshots.

    Annotating the SAVED image (not the live stream) is deliberate: an operator
    reviewing footage needs to see why the app fired, and the stream needs to
    stay cheap.

    A violating detection is drawn red; an ordinary one amber, so a reviewer can
    tell at a glance which box tripped the rule without reading the event.

    `links` are proximity lines between two objects' centres, frame pixels:
    [(x1, y1, x2, y2, within_threshold, dist_px)] -- red when within, grey
    otherwise, distance written at the midpoint. `bubbles` are guard zones
    grown around an object's box: [(x1, y1, x2, y2, intruded)] -- red while
    intruded. `trails` are movement histories: [([(x, y), ...], live)] --
    drawn as a DOTTED line behind each tracked object (each stored point is
    one dot; the engine stores a point only when the object moved, so dot
    spacing is movement). All come from the FunctionEngine.
    """
    if frame is None:
        return frame
    shapes = _zone_shapes(zone, overlays)

    # A numpy frame on a box WITHOUT cv2 (the synthetic camera produces
    # exactly this pairing): drawn via PIL, then converted back so the
    # caller still holds the shape a real camera returns. Without this the
    # ndarray fell into the PIL branch below on its `.copy` attribute alone,
    # and `out.size` -- an int on an array, a (w, h) tuple on an image --
    # blew up Image.new with "Size must be a list or tuple".
    back_to_np = None
    if cv2 is None and Image is not None and hasattr(frame, "shape"):
        try:
            import numpy as np                              # noqa: PLC0415

            frame = Image.fromarray(frame[:, :, ::-1])      # BGR -> RGB
            back_to_np = np
        except Exception:                                   # noqa: BLE001
            return frame

    if cv2 is not None and hasattr(frame, "shape"):
        out = frame.copy()
        h, w = out.shape[:2]
        # Trails at the very bottom: history must never cover a live box or
        # label. One dot per stored point; the newest end is brighter so the
        # direction of travel reads at a glance. A trail whose object has
        # LEFT the frame dims to grey while it ages out.
        for pts, live in trails or []:
            n = len(pts)
            for i, (tx, ty) in enumerate(pts):
                if not (0 <= tx < w and 0 <= ty < h):
                    continue
                if live:
                    # 40%..100% brightness, oldest to newest.
                    k = 0.4 + 0.6 * (i / max(1, n - 1))
                    color = (int(60 * k), int(200 * k), int(255 * k))
                else:
                    color = (128, 128, 128)
                cv2.circle(out, (int(tx), int(ty)),
                           3 if i == n - 1 else 2, color, -1)
        # Bubbles first, under everything: a translucent fill would hide box
        # labels drawn before it.
        for x1, y1, x2, y2, intruded in bubbles or []:
            color = (0, 0, 255) if intruded else (200, 160, 60)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            region = out[y1:y2, x1:x2]
            tint = region.copy()
            tint[:] = color
            cv2.addWeighted(tint, 0.18, region, 0.82, 0, region)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        for pts, is_line, color, facing in shapes:
            if is_line:
                cv2.line(out, pts[0], pts[1], color, 2)
                arrow = _face_arrow(pts, facing)
                if arrow:
                    # tipLength is a FRACTION of the shaft, so a short arrow
                    # keeps a head in proportion to itself.
                    cv2.arrowedLine(out, arrow[0], arrow[1], color, 2,
                                    tipLength=0.35)
            else:
                import numpy as _np                       # noqa: PLC0415
                # Filled first, outlined after, so the gradient never washes
                # over the edge that defines where the zone actually ends.
                _poly_grad(out, pts, color)
                cv2.polylines(out, [_np.array(pts, dtype=_np.int32)], True, color, 2)
        for d in detections or []:
            x1, y1, x2, y2 = d["box"]
            rgb = _det_rgb(d)
            color = ((rgb[2], rgb[1], rgb[0]) if rgb            # cv2 is BGR
                     else (0, 0, 255) if d.get("violation")
                     else (0, 170, 255))
            # Boxes stay OUTLINE-only, deliberately. A zone is one fill per
            # frame; boxes are one per OBJECT per frame, and the float blend
            # behind the gradient was measurable CPU on a Pi already running
            # inference -- spent on decoration for the part of the picture
            # (the object itself) that must stay unobscured anyway.
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            # Confidence as a percentage -- "person 87%" reads at a glance
            # where "person 0.87" reads like a coordinate. The track id (PID)
            # in front names the individual, so a reviewer can follow one
            # object across frames and events.
            tid = d.get("track_id")
            tag = (f"#{tid} " if tid is not None else "")
            cv2.putText(out, f"{tag}{d['label']} {d['confidence'] * 100:.0f}%",
                        (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            # Size and distance UNDER the box, where the depth camera
            # measured them -- on the object, not in a list beside it.
            size = size_text(d)
            if size:
                cv2.putText(out, size, (x1, min(out.shape[0] - 4, y2 + 15)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            # ...and the measured lines themselves, green so they read as
            # measurements and not as another box: each side where it was
            # measured with its size on it, a ring round a round thing.
            segs, ring, silhouette = measure_lines(d)
            green = (82, 197, 35)                                # BGR
            if len(silhouette) >= 3:
                poly = _np_pts([(int(round(px)), int(round(py))) for px, py in silhouette])
                # A faint fill so the shape reads as the object, a firm edge
                # so the outline reads as the extraction.
                layer = out.copy()
                cv2.fillPoly(layer, [poly], green)
                cv2.addWeighted(layer, 0.18, out, 0.82, 0, out)
                cv2.polylines(out, [poly], True, green, 2, cv2.LINE_AA)
            for (ax, ay), (bx, by), text in segs:
                a, b = (int(round(ax)), int(round(ay))), (int(round(bx)), int(round(by)))
                cv2.line(out, a, b, green, 2)
                cv2.circle(out, a, 3, green, -1)
                cv2.circle(out, b, 3, green, -1)
                if text:
                    _size_tag(out, text, ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2), green)
            if ring is not None:
                (cx, cy), r, text = ring
                cv2.circle(out, (int(round(cx)), int(round(cy))), max(1, int(round(r))), green, 2)
                if text:
                    _size_tag(out, text, (int(round(cx)), int(round(cy))), green)
        for x1, y1, x2, y2, within, dist_px in links or []:
            color = (0, 0, 255) if within else (170, 170, 170)
            cv2.line(out, (x1, y1), (x2, y2), color, 2)
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            cv2.putText(out, f"{dist_px:.0f}px", (mx + 4, max(12, my - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        return out

    if Image is not None and hasattr(frame, "copy"):
        out = frame.copy()
        # The same gradient as the cv2 path, done the way PIL does it: the
        # fills go on a transparent layer that is composited once, because
        # ImageDraw cannot blend into the image directly.
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        # Trails first, same bottom-most rule as the cv2 path: history must
        # never cover a live box or label.
        for pts, live in trails or []:
            n = len(pts)
            for i, (tx, ty) in enumerate(pts):
                if live:
                    k = 0.4 + 0.6 * (i / max(1, n - 1))
                    rgb = (int(255 * k), int(200 * k), int(60 * k))
                else:
                    rgb = (128, 128, 128)
                r = 3 if i == n - 1 else 2
                ld.ellipse((tx - r, ty - r, tx + r, ty + r), fill=rgb)

        def _bands(shape_pts, rgb, lo=0.05, hi=0.30, steps=12):
            ys = [int(p[1]) for p in shape_pts]
            top, bot = min(ys), max(ys)
            if bot - top < 2:
                return
            for i in range(steps):
                y0 = top + (bot - top) * i // steps
                y1b = top + (bot - top) * (i + 1) // steps
                if y1b <= y0:
                    continue
                a = int(255 * (lo + (hi - lo) * (i + 0.5) / steps))
                strip = Image.new("RGBA", out.size, (0, 0, 0, 0))
                sd = ImageDraw.Draw(strip)
                sd.polygon(shape_pts, fill=(*rgb, a))
                # Keep only this horizontal band of the shape.
                sd.rectangle((0, 0, out.size[0], y0), fill=(0, 0, 0, 0))
                sd.rectangle((0, y1b, out.size[0], out.size[1]), fill=(0, 0, 0, 0))
                layer.alpha_composite(strip)

        for x1, y1, x2, y2, intruded in bubbles or []:
            rgb = (220, 40, 60) if intruded else (90, 170, 210)
            ld.rectangle((x1, y1, x2, y2), outline=rgb, width=2)
        for pts, is_line, color, facing in shapes:
            rgb = (color[2], color[1], color[0])           # BGR -> RGB for PIL
            if is_line:
                ld.line([pts[0], pts[1]], fill=rgb, width=2)
                arrow = _face_arrow(pts, facing)
                if arrow:
                    (ax, ay), (bx, by) = arrow
                    ld.line([(ax, ay), (bx, by)], fill=rgb, width=2)
                    # Head, built from the shaft's own direction so it points
                    # the way the arrow does at any angle.
                    hx, hy = bx - ax, by - ay
                    hlen = math.hypot(hx, hy) or 1.0
                    ux, uy = hx / hlen, hy / hlen
                    back = 0.32 * hlen
                    ld.polygon([(bx, by),
                                (bx - ux * back - uy * back * 0.5,
                                 by - uy * back + ux * back * 0.5),
                                (bx - ux * back + uy * back * 0.5,
                                 by - uy * back - ux * back * 0.5)], fill=rgb)
            else:
                _bands(list(pts), rgb)
                ld.polygon(pts, outline=rgb)
        for det in detections or []:
            color = (_det_rgb(det)
                     or ((220, 40, 60) if det.get("violation")
                         else (230, 150, 40)))
            # Outline-only, same as the cv2 path: one fill per object per
            # frame is real CPU spent hiding the thing being looked at.
            ld.rectangle(det["box"], outline=color, width=2)
            tid = det.get("track_id")
            tag = (f"#{tid} " if tid is not None else "")
            ld.text((det["box"][0], max(2, det["box"][1] - 12)),
                    f"{tag}{det['label']} {det['confidence'] * 100:.0f}%", fill=color)
            size = size_text(det)
            if size:
                ld.text((det["box"][0], det["box"][3] + 2), size, fill=color)
            segs, ring, silhouette = measure_lines(det)
            green = (35, 197, 82)
            if len(silhouette) >= 3:
                ld.polygon(silhouette, outline=green, width=2)

            def tag(text, at):
                l, t, r_, b_ = ld.textbbox(at, text)
                ld.rectangle([l - 3, t - 2, r_ + 3, b_ + 2], fill=green)
                ld.text(at, text, fill=(0, 0, 0))
            for a, b, text in segs:
                ld.line([a, b], fill=green, width=2)
                if text:
                    tag(text, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 6))
            if ring is not None:
                (cx, cy), r, text = ring
                ld.ellipse([cx - r, cy - r, cx + r, cy + r], outline=green, width=2)
                if text:
                    tag(text, (cx + 4, cy - 6))
        for x1, y1, x2, y2, within, dist_px in links or []:
            rgb = (220, 40, 60) if within else (170, 170, 170)
            ld.line([(x1, y1), (x2, y2)], fill=rgb, width=2)
            ld.text(((x1 + x2) // 2 + 4, max(2, (y1 + y2) // 2 - 12)),
                    f"{dist_px:.0f}px", fill=rgb)
        result = Image.alpha_composite(out.convert("RGBA"),
                                       layer).convert(out.mode)
        if back_to_np is not None:
            return back_to_np.asarray(result)[:, :, ::-1].copy()  # RGB -> BGR
        return result
    return frame
