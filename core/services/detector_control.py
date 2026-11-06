# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
detector_control.py -- run and shape the LOCAL detection app from the dashboard.

The standalone board runs its detection app (systems/<slug>/app/run.py) right
beside its master. This service is how the Detection page drives that app without
an SSH session: read what it is doing, change its camera / zones / model by
writing config.local.toml (the app hot-reloads), and start or stop the process.

It never reaches across the network -- everything here is same-box file writes
and a local process, which is the whole point of a standalone board. A master
that controls devices over the fleet uses the control channel instead
(core/services/control.py); the two do not overlap.

Writes touch only the gitignored config.local.toml, one key at a time, so the
committed config.toml stays the human-owned defaults and a camera change never
disturbs the zones beside it.
"""

import contextlib
import functools
import glob
import importlib.util
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time

try:
    import tomllib                     # Python 3.11+
except ModuleNotFoundError:            # pragma: no cover
    import tomli as tomllib

from core import config as config_mod
from core import tomlcache
from core.services import modbus_watchdog
from core.services import power_state
from core.services import redact as _redact
from core.services import snapshot_meta as _snapshot_meta

DEFAULT_STREAM_PORT = 5102


def _run(argv, timeout=6, merge=True):
    """Run a probe tool and return its output.

    `merge` folds stderr in, which is what the v4l2 and Aravis callers want:
    they scrape with regexes, some of these tools report on stderr, and a
    stray line costs nothing.

    It is fatal for a tool whose output is PARSED AS A WHOLE, and ffprobe is
    one: `-of json` writes JSON to stdout while the H.264 decoder writes
    "[h264 @ ...] error while decoding MB 0 23" to stderr for perfectly
    ordinary RTSP streams. Concatenated, json.loads sees the warning first and
    raises -- so every IP camera that emitted one decode warning reported
    "could not read the stream", on a probe that had just succeeded.
    """
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        text = out.stdout or ""
        return text + (out.stderr or "") if merge else text
    except (OSError, subprocess.SubprocessError):
        return ""


#: An RTSP probe is a network round trip to a camera that may be busy serving
#: the pipeline as well, so it gets longer than a local device query. TCP
#: because a camera behind a switch that drops the UDP interleave answers the
#: RTSP handshake and then times out waiting for packets that never arrive --
#: which reads as "unreachable" for a camera that is plainly reachable.
_FFPROBE_RTSP = ("-rtsp_transport", "tcp")
_FFPROBE_TIMEOUT = 12


def _ffprobe(url, entries):
    """One stream's fields, as ffprobe reports them, or {}."""
    argv = ["ffprobe", "-v", "error"]
    if str(url).lower().startswith("rtsp://"):
        argv += list(_FFPROBE_RTSP)
    argv += ["-select_streams", "v:0", "-show_entries", f"stream={entries}",
             "-of", "json", str(url)]
    text = _run(argv, timeout=_FFPROBE_TIMEOUT, merge=False)
    try:
        return (json.loads(text).get("streams") or [{}])[0] or {}
    except (ValueError, KeyError, IndexError, TypeError):
        return {}


def _pkg_dir(systems_dir, slug):
    return os.path.join(str(systems_dir), slug)


#: path -> last successfully parsed content. A transient read failure (EBUSY,
#: a slow SD card, a signal-interrupted read) used to parse as {} -- which the
#: System Control table then displayed as "camera gone, model gone" for one
#: poll and back again the next. A config file does not blink in and out of
#: existence; when a file that parsed before suddenly reads as nothing, the
#: previous truth is the better answer.
#:
#: Since perf item L3 the parsed copy is also the ANSWER while the file is
#: unchanged (core/tomlcache.py, keyed by mtime and size): it used to be
#: re-parsed on every read, eight times per dashboard poll. This name stays
#: for code that pops from it -- popping now means "parse it again next time".
_toml_cache = tomlcache.compat


def _read_toml(path):
    """The parsed file -- a copy the caller may change -- re-parsed only when
    it changed on disk; the last good copy when it cannot be read now."""
    return tomlcache.read(path, fallback=True)


def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _merged(pkg_dir):
    cfg = {}
    for name in ("config.toml", "config.local.toml"):
        cfg = _merge(cfg, _read_toml(os.path.join(pkg_dir, name)))
    return cfg


def _local_path(pkg_dir):
    return os.path.join(pkg_dir, "config.local.toml")


#: Serialises the read-modify-write of a system's config.
#:
#: Every writer here does read the whole document -> change one table -> write
#: it back, and the routes that call them run in a threadpool, so two requests
#: that land together (saving zones while a workflow recompiles, adding a
#: camera while an output is edited) could both read the OLD document and the
#: second write would silently drop the first one's change. The write itself
#: was already atomic; that never made the sequence atomic.
#:
#: Callers hold this across the read AND the write via config_lock(). It is
#: the IN-PROCESS half: the hub and a master are two processes writing the
#: same file, so every read-modify-write also holds the file's own OS lock
#: (cfg_lock.held -- see pkg_config_lock).
_config_lock = threading.RLock()


@contextlib.contextmanager
def pkg_config_lock(pkg_dir):
    """Both halves of the config lock for one system's config.local.toml:
    this process's lock and the cross-process file lock. Re-entrant."""
    from core.services import cfg_lock                          # noqa: PLC0415
    with _config_lock:
        with cfg_lock.held(_local_path(pkg_dir)):
            yield


def config_lock():
    """The lock guarding a system's config read-modify-write. Reentrant, so a
    writer that calls another writer does not deadlock."""
    return _config_lock


def locked(fn):
    """Run a whole read-modify-write function under the config lock.

    Applied to the setters below rather than re-indenting each body: what has
    to be atomic is the SEQUENCE (read the document, change one table, write
    it back), and the function boundary is exactly that sequence.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Every locked setter is (systems_dir, slug, ...): the system's own
        # file lock too, so the hub and the master cannot interleave.
        try:
            pkg = _pkg_dir(args[0], args[1]) if len(args) >= 2 else None
        except Exception:                                      # noqa: BLE001
            pkg = None
        if pkg is None:
            with _config_lock:
                return fn(*args, **kwargs)
        with pkg_config_lock(pkg):
            return fn(*args, **kwargs)
    return wrapper


@contextlib.contextmanager
def edit_local(pkg_dir):
    """Read config.local.toml, hand it over to be changed, write it back --
    all while holding the lock, so a concurrent editor cannot base its write
    on the document this one is about to replace.

        with dc.edit_local(pkg_dir) as local:
            local["functions"] = cleaned

    The body may raise: nothing is written then, which is the behaviour a
    validation failure wants.
    """
    with pkg_config_lock(pkg_dir):
        # Read FRESH under the lock: the cache may hold the copy from before
        # the other process's write this lock just waited for.
        _toml_cache.pop(_local_path(pkg_dir), None)
        local = _read_toml(_local_path(pkg_dir))
        yield local
        _write_local(pkg_dir, local)


def _write_local(pkg_dir, local):
    from core.services import cfg_lock                          # noqa: PLC0415
    path = _local_path(pkg_dir)
    with pkg_config_lock(pkg_dir):
        # A temp name of this process and call, fsync'd, then one replace:
        # two writers can no longer interleave into the same .tmp file.
        cfg_lock.write(path, config_mod.toml_text(local))
        # The next read must not serve the copy from before this write.
        _toml_cache.pop(path, None)


def _data_dir(pkg_dir, cfg):
    """Where this system's events and snapshots live.

    ASK THE APP. It owns the rule -- prefer an explicit [app].data_dir, else the
    NVMe (/mnt/nvme/cytron/<slug>/data), else the package's ./data on the eMMC
    (systems/*/app/config.py data_dir) -- and a second implementation here got
    it wrong: it resolved to the eMMC unconditionally, so the app wrote
    snapshots to the NVMe while Footage listed an empty folder beside the
    package, and the eMMC dir was created as a side effect of looking.

    app/config.py imports nothing but the standard library, so it loads by file
    path exactly like app/runtime.py does. If it cannot be loaded (an older
    package), fall back to the previous behaviour rather than failing.
    """
    module = _load_app_module(pkg_dir, "config")
    if module is not None and hasattr(module, "data_dir"):
        try:
            return module.data_dir(cfg, pkg_dir)
        except Exception:               # noqa: BLE001
            pass
    rel = (cfg.get("app") or {}).get("data_dir") or "data"
    path = rel if os.path.isabs(rel) else os.path.join(pkg_dir, rel)
    os.makedirs(path, exist_ok=True)
    return path


#: (path) -> (mtime, module). These files change on a git pull, not per
#: request -- and re-exec'ing them from disk on EVERY status poll meant any
#: transient I/O failure surfaced as "no runtime" for that one poll, which the
#: System Control table rendered as the system blinking off and on.
_app_module_cache = {}


def _load_app_module(pkg_dir, name):
    """Load systems/<slug>/app/<name>.py standalone, by file path, cached until
    the file's mtime changes. The app imports nothing from core/, which is what
    makes loading it here safe."""
    path = os.path.join(pkg_dir, "app", f"{name}.py")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _app_module_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    spec = importlib.util.spec_from_file_location(
        f"_app{name}_{abs(hash(pkg_dir))}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:                   # noqa: BLE001
        # Keep serving the previous good module rather than answering "no
        # runtime" because one exec failed mid-poll.
        return cached[1] if cached else None
    _app_module_cache[path] = (mtime, module)
    return module


def _app_runtime(pkg_dir):
    """The package's own app/runtime.py. Using the app's OWN start/stop keeps
    process handling (pid file, staleness, Windows vs POSIX) in one place
    rather than reimplemented here."""
    return _load_app_module(pkg_dir, "runtime")


def stream_port(systems_dir, slug):
    cfg = _merged(_pkg_dir(systems_dir, slug))
    return int((cfg.get("app") or {}).get("stream_port") or DEFAULT_STREAM_PORT)


def capabilities(systems_dir, slug):
    """What the LOCAL detection app provides, in the same vocabulary a paired
    device's adapter declares. A standalone board has no agent to declare them,
    and without these every capability-gated widget (the zone tiles) and page
    stays hidden even while detection runs. Cheap: two TOML reads, no process
    probing."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    if not os.path.exists(os.path.join(pkg_dir, "app", "runtime.py")):
        return set()                    # no local detection app at all
    caps = {"stream", "footage", "snapshots", "analytics.daily",
            "control.device", "control.camera", "control.detection"}
    if _merged(pkg_dir).get("zones"):
        caps.add("settings.zones")
    return caps


def _zone_guidance(template):
    """This template's zone requirement + hint (provision.ZONE_GUIDANCE)."""
    try:
        from core.services import provision                 # noqa: PLC0415
        return provision.zone_guidance(template)
    except Exception:                                       # noqa: BLE001
        return {"mode": "optional", "hint": ""}


# -- camera discovery + preview ----------------------------------------------
#
# A Raspberry Pi exposes many /dev/video* nodes that are NOT cameras: the ISP
# pipeline (pispbe-*), the hardware codec (rpi-hevc-dec), and one metadata node
# per real camera. Listing them all makes the picker unusable. So each node is
# asked, over the V4L2 QUERYCAP ioctl, what it actually is, and only genuine
# video-capture devices are kept -- no name guessing, the kernel's own answer.

_VIDIOC_QUERYCAP = (2 << 30) | (104 << 16) | (ord("V") << 8) | 0
_QUERYCAP_FMT = "16s32s32sIII3I"                # struct v4l2_capability, 104 bytes
_CAP_VIDEO_CAPTURE = 0x00000001
_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
_CAP_VIDEO_M2M = 0x00008000
_CAP_VIDEO_M2M_MPLANE = 0x00004000


def _v4l2_caps(path):
    """(device_caps, card_name, bus_info) from a V4L2 node, or (None, None,
    None). Opens non-blocking so a camera the detector is USING still answers
    QUERYCAP."""
    try:
        import fcntl                                        # noqa: PLC0415 (Linux only)
        import struct                                       # noqa: PLC0415
    except ImportError:
        return None, None, None
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        # EBUSY: a real capture device already in use (very likely the running
        # camera). Keep it -- we just cannot read its card name here.
        import errno                                        # noqa: PLC0415
        if e.errno == errno.EBUSY:
            return _CAP_VIDEO_CAPTURE, None, None
        return None, None, None
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buf, True)
        driver, card, bus, ver, caps, device_caps, *_ = struct.unpack(_QUERYCAP_FMT, bytes(buf))
        name = card.split(b"\x00", 1)[0].decode(errors="replace").strip()
        where = bus.split(b"\x00", 1)[0].decode(errors="replace").strip()
        return (device_caps or caps), (name or None), (where or None)
    except OSError:
        return None, None, None
    finally:
        os.close(fd)


def _is_capture(caps):
    if not caps:
        return False
    is_capture = caps & (_CAP_VIDEO_CAPTURE | _CAP_VIDEO_CAPTURE_MPLANE)
    is_m2m = caps & (_CAP_VIDEO_M2M | _CAP_VIDEO_M2M_MPLANE)
    return bool(is_capture and not is_m2m)


#: How long a USB camera scan is reused (perf item L4). The scan opens and
#: queries every /dev/video* node, and the dashboard -- through cameras() and
#: _refresh_usb_names -- asked for one on every poll, every 600 ms per open
#: page. Camera Setup's list asks with fresh=True, so plugging a camera in
#: and pressing refresh still shows it at once.
LIST_CAMERAS_TTL_S = 5.0
_cam_scan = {"at": 0.0, "value": None}
_cam_scan_lock = threading.Lock()


def list_cameras(fresh=False):
    """The USB cameras, from a scan at most LIST_CAMERAS_TTL_S old (or a new
    one when `fresh`). Each call gets its own copies of the entries."""
    now = time.monotonic()
    with _cam_scan_lock:
        hit = _cam_scan["value"]
        if not fresh and hit is not None and now - _cam_scan["at"] < LIST_CAMERAS_TTL_S:
            return [dict(c) for c in hit]
    value = _scan_cameras()
    with _cam_scan_lock:
        _cam_scan.update(at=time.monotonic(), value=value)
    return [dict(c) for c in value]


def forget_camera_scan():
    """Drop the cached scan: the next list_cameras() scans again."""
    with _cam_scan_lock:
        _cam_scan.update(at=0.0, value=None)


def _scan_cameras():
    """The USB cameras this box exposes through V4L2.

    Two filters, both the kernel's own answer rather than name guessing:
    the node must be a video-CAPTURE device (drops metadata and codec nodes),
    and it must sit on the USB bus (drops the Pi's ISP back-end, which
    presents eight `pispbe` capture nodes that pass the first test and filled
    the picker with devices no camera is attached to).

    A node that cannot be asked -- no V4L2 on a dev laptop, or busy because
    the detector is already streaming it -- is kept: an unanswered question is
    not a reason to hide the camera currently running. RTSP/IP and GigE
    cameras are entered by URL, not discovered here.
    """
    # udev slot symlinks: /dev/cam-usb<slot> is pinned to a PHYSICAL socket.
    # A node behind a slot is offered under the SLOT number, so a saved
    # "camera 0" keeps meaning "socket 0" across replugs and re-enumeration.
    slots = {}
    for link in glob.glob("/dev/cam-usb[0-9]*"):
        m = re.search(r"(\d+)$", link)
        real = os.path.realpath(link)
        if m and os.path.exists(real):
            slots[real] = int(m.group(1))
    cams = []
    for path in sorted(glob.glob("/dev/video*"),
                       key=lambda p: int(re.search(r"(\d+)$", p).group(1)) if re.search(r"(\d+)$", p) else 0):
        m = re.search(r"(\d+)$", path)
        index = int(m.group(1)) if m else None
        caps, card, bus = _v4l2_caps(path)
        # On a host without V4L2 (dev laptop) caps is None -- list the node
        # anyway so the box is still selectable; on a Pi, keep only capture nodes.
        if caps is not None and not _is_capture(caps):
            continue
        # platform:pispbe, platform:rpi-hevc-dec, and the CSI receiver all
        # answer "capture". Only what is plugged into a USB port is a camera
        # an operator can point at something.
        if bus and not bus.lower().startswith("usb"):
            continue
        name = card
        if not name and index is not None:
            try:
                with open(f"/sys/class/video4linux/video{index}/name",
                          encoding="utf-8") as f:
                    name = f.read().strip()
            except OSError:
                pass
        slot = slots.get(os.path.realpath(path))
        if slot is not None:
            index = slot
        cams.append({"index": index, "path": path, "name": name or path,
                     "kind": "usb", "slot": slot})
    return cams


def usb_ports(systems_dir=None):
    """The board's physical USB sockets, with what is in each and who uses it.

    [{slot, present, name, device, used_by}] for every /dev/cam-usb<slot>
    slot the udev rules pin (plus any camera on an unpinned node, slot None).
    `used_by` lists the RUNNING systems whose enabled cameras capture that
    socket -- consumers of the shared feed included, because "can I unplug
    this?" is the question the panel answers and a consumer's picture dies
    with the device exactly like the owner's.
    """
    cams = {c.get("slot"): c for c in list_cameras() if c.get("slot") is not None}
    unpinned = [c for c in list_cameras() if c.get("slot") is None]
    # Which running systems capture which numeric source. Best-effort, like
    # sources_elsewhere: a package that cannot be read blames nobody.
    users = {}
    if systems_dir:
        try:
            from core.services import provision as provision_service  # noqa: PLC0415
            for slug, pkg in provision_service.system_slugs(systems_dir).items():
                try:
                    if not ai_running(systems_dir, slug):
                        continue
                    for c in _camera_list(_merged(pkg)):
                        src = str(c.get("source") or "").strip()
                        if c.get("enabled", True) and src.isdigit():
                            users.setdefault(int(src), []).append(slug)
                except Exception:                           # noqa: BLE001
                    continue
        except Exception:                                   # noqa: BLE001
            pass
    out = []
    slots = sorted({int(re.search(r"(\d+)$", p).group(1))
                    for p in glob.glob("/dev/cam-usb[0-9]*")} | set(cams))
    for slot in slots or (0, 1):
        cam = cams.get(slot)
        out.append({"slot": slot,
                    "present": cam is not None,
                    "name": (cam or {}).get("name"),
                    "device": (cam or {}).get("path"),
                    "used_by": sorted(set(users.get(slot, [])))})
    for cam in unpinned:
        out.append({"slot": None, "present": True, "name": cam.get("name"),
                    "device": cam.get("path"),
                    "used_by": sorted(set(users.get(cam.get("index"), [])))})
    return out


#: GigE Vision's control port. Cameras listen here for the discovery command
#: and answer with everything needed to find them: IP, MAC, vendor, model,
#: serial, and the name somebody gave them.
GVCP_PORT = 3956

#: DISCOVERY_CMD, as GigE Vision defines it: magic 0x42, flags 0x11 (answer
#: required, broadcast answer allowed), command 0x0002, no payload, request id
#: 1. Eight bytes, and every camera on the wire answers it.
_GVCP_DISCOVERY = b"\x42\x11\x00\x02\x00\x00\x00\x01"

#: Where each field sits in the 248-byte DISCOVERY_ACK payload.
_GVCP_MAC = 10                          # 6 bytes
_GVCP_IP = 44                           # 4 bytes, the camera's current address
_GVCP_VENDOR = 72                       # 32 bytes, NUL-padded
_GVCP_MODEL = 104                       # 32
_GVCP_SERIAL = 216                      # 16
_GVCP_USER_NAME = 232                   # 16


def _local_ipv4s():
    """Every IPv4 address this box has, with its broadcast address.

    Discovery is a BROADCAST, and a box with two NICs (the common shape for a
    GigE camera -- one leg to the site network, one to the camera) has to ask
    on both: a packet sent from the wrong interface never reaches the camera.
    Returns [(address, broadcast)].
    """
    out = []
    text = _run(["ip", "-o", "-4", "addr", "show"])
    for line in text.splitlines():
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        addr, bits = m.group(1), int(m.group(2))
        if addr.startswith("127."):
            continue
        try:
            packed = struct.unpack("!I", socket.inet_aton(addr))[0]
            mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
            bcast = socket.inet_ntoa(struct.pack("!I", packed | (~mask & 0xFFFFFFFF)))
        except (OSError, struct.error):
            continue
        out.append((addr, bcast))
    if out:
        return out
    # No `ip` (a dev box, a container): the address this box would route from
    # is better than nothing, and its /24 broadcast covers the usual case.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        addr = probe.getsockname()[0]
        probe.close()
        return [(addr, addr.rsplit(".", 1)[0] + ".255")]
    except OSError:
        return []


def _gvcp_text(payload, offset, length):
    """One NUL-padded ASCII field out of a discovery answer."""
    raw = payload[offset:offset + length]
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def scan_gige(timeout=1.2):
    """Ask the network which GigE Vision cameras are on it, and where.

    A UDP broadcast of the protocol's own DISCOVERY_CMD, sent from every local
    address, and whatever answers. No Aravis, no tools, no dependency: this is
    eight bytes on port 3956 and a 248-byte reply, which is the whole protocol
    for "who is out there".

    Why not leave it to arv-tool: it only lists cameras Aravis can already
    open, so the two failures an installer actually hits -- the plugin is not
    installed, or the camera is on a different subnet from this box -- both
    look identical to "no camera found", with nothing to act on. A camera that
    answers here is plugged in and powered, whatever Aravis thinks, and its
    address is the fact that tells the installer which of those it is.

    Returns [{"ip", "mac", "vendor", "model", "serial", "user_name", "name"}].
    """
    found = {}
    for addr, bcast in _local_ipv4s():
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.2)
            sock.bind((addr, 0))
            for target in (bcast, "255.255.255.255"):
                try:
                    sock.sendto(_GVCP_DISCOVERY, (target, GVCP_PORT))
                except OSError:
                    continue
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, src = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                # DISCOVERY_ACK: magic 0x00, status, command 0x0003.
                if len(data) < 8 + 248 or data[2:4] != b"\x00\x03":
                    continue
                body = data[8:]
                ip = socket.inet_ntoa(body[_GVCP_IP:_GVCP_IP + 4])
                if ip == "0.0.0.0":
                    ip = src[0]         # answered before it had its address
                mac = ":".join(f"{b:02x}" for b in body[_GVCP_MAC:_GVCP_MAC + 6])
                cam = {
                    "ip": ip, "mac": mac,
                    "vendor": _gvcp_text(body, _GVCP_VENDOR, 32),
                    "model": _gvcp_text(body, _GVCP_MODEL, 32),
                    "serial": _gvcp_text(body, _GVCP_SERIAL, 16),
                    "user_name": _gvcp_text(body, _GVCP_USER_NAME, 16),
                    # Which of THIS box's addresses heard it, so "the camera is
                    # on the other network" can be said rather than guessed.
                    "via": addr,
                }
                # Aravis names a device "<vendor>-<model>-<serial>" -- checked
                # against arv-tool on a real Hikrobot: "Hikrobot-MV-CS050-60GC-
                # DB0176142". That is the string aravissrc opens, so building
                # the same one here means a camera found ONLY by the scan can
                # still be selected and will still open. A user-defined name
                # wins, because Aravis accepts that too and it is what the
                # person who set it expects to see.
                cam["name"] = (cam["user_name"]
                               or "-".join(p for p in (cam["vendor"],
                                                       cam["model"],
                                                       cam["serial"]) if p)
                               or ip)
                found[mac or ip] = cam
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return sorted(found.values(), key=lambda c: c["ip"])


def _same_subnet(a, b, bits=24):
    """Cheap "are these two on the same network" for the /24 case, which is
    what a site network and a camera leg both are in practice."""
    try:
        pa = struct.unpack("!I", socket.inet_aton(a))[0]
        pb = struct.unpack("!I", socket.inet_aton(b))[0]
    except OSError:
        return True                     # cannot tell: do not cry wolf
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    return (pa & mask) == (pb & mask)


#: GigE Vision bootstrap register: the Control Channel Privilege. Non-zero
#: means some application holds control of the camera, and the camera then
#: refuses every register write from anyone else -- which is what an operator
#: sees as "the camera gave no frames".
_GVCP_CCP = 0x0A00
_GVCP_READREG = b"\x42\x01\x00\x80"


def _gvcp_read_register(ip, address, timeout=1.0):
    """One GigE Vision READREG, sessionlessly. The value, or None.

    No Aravis and no control of the camera: READREG is answered by a camera
    somebody else is streaming, which is the whole point -- this is the
    question "is another application holding you", asked of the camera itself.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(_GVCP_READREG + struct.pack("!HHI", 4, 7, address),
                    (ip, GVCP_PORT))
        data, _src = sock.recvfrom(1024)
    except OSError:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    # status 0 = ok; anything else (0x8003 "invalid address" on cameras that do
    # not implement a register) is not an answer.
    if len(data) < 12 or struct.unpack("!H", data[0:2])[0] != 0:
        return None
    return struct.unpack("!I", data[8:12])[0]


def _gige_controlled(source, found=None):
    """True when this GigE camera is held by ANOTHER application.

    Asked of the camera, not guessed from a failure: a camera whose control
    channel is taken answers reads and refuses writes, so "cannot open" and
    "somebody else has it" look identical from OpenCV -- and they send the
    operator to completely different places.
    """
    name = str(source).split(":", 1)[1] if ":" in str(source) else ""
    ip = next((c["ip"] for c in (scan_gige() if found is None else found)
               if not name or c.get("name") == name), "")
    if not ip:
        return False
    return bool(_gvcp_read_register(ip, _GVCP_CCP))


def _aravis_plugin():
    """Is the GStreamer element that OPENS a GigE camera on this box?

    The pipeline opens a camera with `aravissrc`, not with `arv-tool`: the
    element is in libaravis, the tool is in a separate package (`aravis-tools-
    cli` on Debian 13, and it is not a dependency of anything). Judging
    "openable" by the tool therefore called a perfectly working box broken --
    the camera opens, the listing just could not see it. This asks the question
    the pipeline actually asks.
    """
    return bool(_run(["gst-inspect-1.0", "aravissrc"]))


def list_gige_cameras():
    """GigE Vision cameras, from Aravis AND from the network itself.

    Two sources, because they answer different questions. arv-tool lists what
    Aravis can OPEN -- the names aravissrc takes, which is what the pipeline
    needs. The network scan lists what is PLUGGED IN, which is what an
    installer needs when nothing shows up: a camera that answers discovery but
    is missing from Aravis is a software or a subnet problem, and both are
    fixable once you can see that the camera is there.

    Merged on IP, then on serial, so one camera is one entry however it was
    found. `opens` says whether Aravis knows it; `note` says what to do when
    it does not.
    """
    text = ""
    for tool in ("arv-tool-0.8", "arv-tool"):
        text = _run([tool])
        if text:
            break
    cams = []
    for line in text.splitlines():
        line = line.strip()
        # arv-tool prints e.g. "Basler-abc123 (192.168.1.40)" per camera.
        if line and "(" in line:
            name = line.split("(", 1)[0].strip()
            if not name:
                continue
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line)
            cams.append({"name": name, "detail": line, "kind": "gige",
                         "ip": m.group(1) if m else "", "opens": True})
    by_ip = {c["ip"]: c for c in cams if c.get("ip")}
    plugin = _aravis_plugin()
    for cam in scan_gige():
        known = by_ip.get(cam["ip"])
        if known:
            # Aravis found it too: keep ITS name (that is what opens the
            # camera) and add what the network answered about it.
            known.update({k: v for k, v in cam.items()
                          if k not in ("name", "kind")})
            continue
        label = " ".join(p for p in (cam["vendor"], cam["model"]) if p)
        reachable = _same_subnet(cam["via"], cam["ip"])
        cams.append({
            "name": cam["name"], "kind": "gige", "ip": cam["ip"],
            "mac": cam["mac"], "vendor": cam["vendor"], "model": cam["model"],
            "serial": cam["serial"], "via": cam["via"],
            "detail": f"{label or cam['name']} ({cam['ip']})",
            # It answered the protocol, so it is powered and on the wire.
            # Whether it OPENS is a separate question, and the answer is the
            # aravissrc element -- not arv-tool, which is only a listing tool
            # and is packaged apart from the plugin. With the element present
            # and the camera on this box's network, the name built from the
            # scan is the one aravissrc takes, so this entry is selectable.
            "opens": bool(plugin and reachable),
            "note": "" if (plugin and reachable) else
                    (f"found at {cam['ip']}, but this box has no address on "
                     f"that network (it asked from {cam['via']}) - give the "
                     "camera an address on this subnet, or this box one on "
                     "the camera's") if not reachable else
                    (f"found at {cam['ip']}, but this box cannot open it - "
                     "the GigE plugin is missing; install libaravis-0.8-0 "
                     "(and aravis-tools-cli for arv-tool)"),
        })
    return cams


#: USB vendor id of the depth cameras this box knows how to open.
ORBBEC_VID = "2bc5"


def list_depth_cameras():
    """Depth cameras on USB, found by VENDOR ID rather than by video node.

    They must not be picked out of the USB list, which is the mistake this
    exists to prevent: an Orbbec presents several V4L2 nodes, and the one a
    slot symlink lands on offers raw Bayer or 16-bit depth -- formats OpenCV
    cannot turn into frames. Chosen from that list, the camera opens, delivers
    nothing, and reports "gave no frames" as though it were broken. It is not:
    it is a camera that has to be opened through its SDK.
    """
    found = []
    for vid_path in sorted(glob.glob("/sys/bus/usb/devices/*/idVendor")):
        try:
            if open(vid_path, encoding="utf-8").read().strip() != ORBBEC_VID:
                continue
            base = os.path.dirname(vid_path)
            def _read(name):
                try:
                    return open(os.path.join(base, name), encoding="utf-8").read().strip()
                except OSError:
                    return ""
            serial = _read("serial")
            found.append({
                "name": _read("product") or "Orbbec depth camera",
                "serial": serial,
                # One camera is plain "orbbec"; a second is named, so a box
                # with two does not depend on enumeration order.
                "source": "orbbec:" + serial if serial else "orbbec",
                "kind": "depth",
            })
        except OSError:
            continue
    return found


def discover_cameras(config_dir=None):
    """Everything discoverable, grouped by connection. RTSP is never here -- it
    is an address, entered by URL. Depth only where it is offered
    (depth_offered): on a customer box an Orbbec plugged in is simply not in
    the list, rather than listed and then refused."""
    # A fresh scan: this is the list somebody just plugged a camera in for.
    forget_camera_scan()
    return {"usb": list_cameras(), "gige": list_gige_cameras(),
            "depth": list_depth_cameras() if depth_offered(config_dir) else [],
            "depth_offered": depth_offered(config_dir)}


#: Offered when a camera cannot be probed, so the operator is never stuck with
#: 640x480 just because this box could not ask. Ordered small to large; the
#: picker adds whatever the probe DID find on top of these.
FALLBACK_RESOLUTIONS = ((640, 480), (1280, 720), (1920, 1080),
                        (2560, 1440), (3840, 2160))


def _v4l2_resolutions(index):
    """Every (w, h) `v4l2-ctl --list-formats-ext` reports for a USB device.

    The driver's own answer, which is the only authority on what a UVC camera
    will actually deliver -- OpenCV silently substitutes the nearest size it
    likes, so asking it instead would report resolutions the camera does not
    have.
    """
    dev = f"/dev/video{index}"
    # A slot number resolves to the node behind the socket's symlink -- the
    # probe must ask the same device open_capture_spec would open.
    link = f"/dev/cam-usb{index}"
    if os.path.islink(link):
        real = os.path.realpath(link)
        if os.path.exists(real):
            dev = real
    text = _run(["v4l2-ctl", "--list-formats-ext", "-d", dev])
    found = set()
    for w, h in re.findall(r"Size:\s*Discrete\s+(\d+)x(\d+)", text):
        found.add((int(w), int(h)))
    # Some drivers report a continuous/stepwise range instead of a list; its
    # maximum is the number that matters here.
    for w, h in re.findall(r"Size:\s*Stepwise.*?Max\s*(\d+)x(\d+)", text, re.S):
        found.add((int(w), int(h)))
    return sorted(found)


#: What each GigE camera answered for its maximum size, by name. A sensor's
#: dimensions do not change while the box is up, and the question costs a
#: subprocess and a full GenICam handshake -- which the pipeline builder now
#: asks on every open.
_GIGE_MAX = {}


def _gige_max_resolution(name=""):
    """(width, height) from a GigE camera's GenICam WidthMax/HeightMax, or None.

    Best-effort, like every other GigE call here: Aravis may not be installed,
    the camera may not answer, and some cameras do not expose the features
    before a full handshake. Unknown is reported as unknown rather than guessed.

    Cached per camera, INCLUDING the unknown answer: a camera that cannot be
    asked must not be asked again on every frame-sized open.
    """
    if name in _GIGE_MAX:
        return _GIGE_MAX[name]
    args = ["--name", name] if name else []
    text = ""
    for tool in ("arv-tool-0.8", "arv-tool"):
        text = _run([tool, *args, "control", "WidthMax", "HeightMax"])
        if text:
            break
    nums = dict(re.findall(r"(WidthMax|HeightMax)\s*=\s*(\d+)", text))
    try:
        top = int(nums["WidthMax"]), int(nums["HeightMax"])
    except (KeyError, ValueError):
        top = None
    _GIGE_MAX[name] = top
    return top


def _stream_resolution(url):
    """(width, height) of what an RTSP/HTTP camera is sending RIGHT NOW.

    NOT the camera's maximum: an IP camera advertises its available profiles
    over ONVIF, which this platform has no client for. What ffprobe can answer
    is what the configured stream currently carries -- a useful floor, and it
    proves the URL and credentials work, but the caller must label it as such
    rather than present it as a ceiling.
    """
    stream = _ffprobe(url, "width,height")
    try:
        return int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, TypeError):
        return None


def camera_resolutions(source, kind=""):
    """What resolutions this camera can be asked for.

    Returns {"candidates": [[w, h], ...], "max": [w, h] or None, "note": str,
    "probed": bool}. `probed` False means the list is the generic fallback and
    the operator is choosing blind -- the UI has to say so, because offering
    4K on a camera that tops out at 720p and silently delivering 720p is how a
    system ends up mis-measuring everything downstream.
    """
    kind = camera_kind(source, kind)
    found, note, probed = [], "", False
    if kind == "usb":
        try:
            index = int(str(source).strip())
        except (TypeError, ValueError):
            index = None
        if index is not None:
            found = _v4l2_resolutions(index)
            probed = bool(found)
            if not probed:
                note = ("could not read this camera's modes (v4l2-ctl missing, "
                        "or the detector is using the device) - the list below "
                        "is generic")
    elif kind == "gige":
        name = str(source).split(":", 1)[1] if ":" in str(source) else ""
        top = _gige_max_resolution(name)
        if top:
            found, probed = [top], True
            note = "the camera's maximum, from its GenICam WidthMax/HeightMax"
        else:
            note = ("could not query this GigE camera (Aravis missing, or it "
                    "does not expose its limits) - the list below is generic")
    elif kind == "rtsp":
        live = _stream_resolution(str(source))
        if live:
            found, probed = [live], True
            note = ("this is what the stream is sending NOW, not necessarily "
                    "the camera's maximum - check its spec sheet and type a "
                    "higher size if it supports one")
        else:
            note = ("could not read the stream (unreachable, or ffprobe is not "
                    "installed) - the list below is generic")
    else:
        note = "this source has no camera to ask"

    candidates = sorted({tuple(r) for r in found} | set(FALLBACK_RESOLUTIONS))
    top = max(found) if found else None
    # The generic list is a fallback for a camera that could not be ASKED, so
    # it must not survive one that answered with a CEILING: this camera tops
    # out at 2448x2048 and the picker went on offering it 2560x1440 and 4K.
    # Picking one of those is not a smaller picture -- the pipeline never
    # negotiates, and the operator is told the camera gave no frames.
    #
    # Only where the answer really is a ceiling. An RTSP probe reports what the
    # stream is sending NOW (its own note says so), and a camera can usually be
    # asked for more, so nothing is trimmed there.
    if probed and top and kind in ("usb", "gige"):
        candidates = [r for r in candidates if r[0] <= top[0] and r[1] <= top[1]]
    return {"candidates": [list(r) for r in candidates],
            "max": list(top) if top else None,
            "note": note, "probed": probed, "kind": kind}


#: Offered when a camera cannot be asked. Ordinary UVC rates, plus the
#: pipeline rates a deployment tends to choose.
FALLBACK_FRAME_RATES = (5, 10, 15, 20, 25, 30)


def _v4l2_frame_rates(index, width=0, height=0):
    """Frame rates `v4l2-ctl --list-formats-ext` reports, for one size.

    The driver's own answer, for the same reason the resolution probe asks it:
    a UVC camera's fastest rate is a property of the MODE, and OpenCV will
    quietly give you a different mode than the one you asked about.

    `v4l2-ctl` prints sizes then their intervals, so the size lines are walked
    and the intervals under the size we care about are collected. With no size
    given, every rate the device offers anywhere is returned -- which is the
    right answer to "what is this camera capable of".
    """
    dev = f"/dev/video{index}"
    link = f"/dev/cam-usb{index}"
    if os.path.islink(link):
        real = os.path.realpath(link)
        if os.path.exists(real):
            dev = real
    text = _run(["v4l2-ctl", "--list-formats-ext", "-d", dev])
    want = (int(width or 0), int(height or 0))
    here, found = None, set()
    for line in text.splitlines():
        size = re.search(r"Size:\s*\w+\s+(\d+)x(\d+)", line)
        if size:
            here = (int(size.group(1)), int(size.group(2)))
            continue
        rate = re.search(r"\(([\d.]+)\s*fps\)", line)
        if rate and (not want[0] or here == want):
            try:
                found.add(round(float(rate.group(1)), 3))
            except ValueError:
                pass
    return sorted(found)


def _gige_rate_facts(name=""):
    """{rate, link_mbps, binning} for a GigE camera, best-effort.

    `ResultingFrameRate` is the camera's OWN answer to "what can I actually
    deliver like this" -- sensor, exposure, size and link together. It is the
    only honest number to show, and no other source has it: the picker's
    generic list said 30 for a camera delivering 1.9.
    """
    args = ["--name", name] if name else []
    text = ""
    for tool in ("arv-tool-0.8", "arv-tool"):
        text = _run([tool, *args, "control", "ResultingFrameRate",
                     "DeviceLinkSpeed", "BinningHorizontal"])
        if text:
            break

    def num(key, cast=float):
        m = re.search(rf"{key}\s*=\s*([\d.]+)", text)
        try:
            return cast(m.group(1)) if m else 0
        except ValueError:
            return 0

    binning = re.search(r"BinningHorizontal\s*=\s*BinningHorizontal(\d+)", text)
    return {"rate": round(num("ResultingFrameRate"), 2),
            "link_mbps": num("DeviceLinkSpeed", int),
            "binning": int(binning.group(1)) if binning else 1}


def camera_frame_rates(source, kind="", width=0, height=0):
    """What frame rates this camera can be asked for, at `width`x`height`.

    Same shape and the same honesty as camera_resolutions: `probed` False
    means the list is generic and the operator is choosing blind. Offering 60
    on a camera that tops out at 15 is how a system ends up quietly running a
    third of the rate its dashboard claims.
    """
    kind = camera_kind(source, kind)
    found, note, probed = [], "", False
    if kind == "usb":
        try:
            index = int(str(source).strip())
        except (TypeError, ValueError):
            index = None
        if index is not None:
            found = _v4l2_frame_rates(index, width, height)
            probed = bool(found)
            if probed and width and height:
                note = f"what this camera offers at {int(width)}x{int(height)}"
            elif probed:
                note = "every rate this camera offers, across its modes"
            else:
                note = ("could not read this camera's modes (v4l2-ctl missing, "
                        "or the detector is using the device) - the list "
                        "below is generic")
    elif kind == "gige":
        # The camera's own ResultingFrameRate, which is the whole truth: a 5MP
        # mono camera on a 100 Mbps link delivers 1.9fps whatever anyone asks
        # for, and the generic list said 30. Naming the LINK matters as much
        # as the number -- 1.9fps reads as a broken camera, and "1.9fps
        # because it is on a 100 Mbps link" reads as a cable to change.
        name = str(source).split(":", 1)[1] if ":" in str(source) else ""
        facts = _gige_rate_facts(name)
        if facts["rate"] > 0:
            found, probed = [facts["rate"]], True
            note = "what this camera can actually deliver as it is set up now"
            if facts["link_mbps"] and facts["link_mbps"] < 1000:
                note = (f"{facts['rate']}fps is all this camera can deliver: "
                        f"it negotiated a {facts['link_mbps']} Mbps link, and "
                        "the picture does not fit down it any faster. Move it "
                        "to a gigabit port (and check the cable), or turn on "
                        "binning below - 2x2 keeps the whole view at a "
                        "quarter of the data.")
            elif facts["rate"] < 10:
                note = (f"{facts['rate']}fps is what this camera reports it "
                        "can deliver at this size and exposure - a shorter "
                        "exposure, a smaller size or binning buys rate")
        else:
            note = ("could not ask this GigE camera for its rate (Aravis "
                    "missing, or another application holds it) - the list "
                    "below is generic")
    elif kind == "rtsp":
        try:
            ratio = _ffprobe(str(source), "avg_frame_rate").get("avg_frame_rate")
            num, den = (int(x) for x in str(ratio).split("/"))
            live = round(num / den, 3) if den else 0.0
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            live = 0.0
        if live > 0:
            found, probed = [live], True
            note = ("what the stream is sending NOW - an RTSP camera's rate "
                    "is set on the camera, not asked for here")
        else:
            # Said out loud, like every other bus says it. This branch left the
            # note EMPTY, so a failed probe on an IP camera showed the generic
            # list with no explanation at all -- the one case where the
            # operator most needs to know they are choosing blind.
            note = ("could not read the stream (unreachable, wrong credentials, "
                    "or ffprobe is not installed) - the list below is generic")
    else:
        note = "this kind of camera does not report its rates - the list is generic"

    candidates = sorted({float(r) for r in found} | {float(r) for r in FALLBACK_FRAME_RATES})
    top = max(found) if found else None
    return {"candidates": candidates, "max": top, "note": note,
            "probed": probed, "kind": kind}


#: How a GigE camera decides its exposure and its gain. Aravis' own enum
#: nicks, passed straight to `aravissrc` -- "on" is what the standard and every
#: camera's manual call CONTINUOUS, and "once" means measure the scene now and
#: hold that setting.
GIGE_AUTO = ("on", "once", "off")


def _gige_auto(value, default="on"):
    v = str(value or "").strip().lower()
    # "continuous" is what the pages and the manuals say; Aravis calls it "on".
    if v in ("continuous", "auto", "true", "1"):
        return "on"
    if v in ("manual", "false", "0"):
        return "off"
    return v if v in GIGE_AUTO else default


# -- camera credentials and the raw-source gate -------------------------------
#
# Pipi's F4 and the camera-save review. Two things the camera routes did not
# hold back: a network camera's PASSWORD went out in every camera list and
# status to any account that could open the system, and the escape hatches
# open_capture_spec accepts (a raw GStreamer pipeline, a file path, FFmpeg's
# other protocols) could be SAVED as a camera by any account granted
# camera.add/change, although the preview already kept them master-only
# (security review F1). And "gige:<name>" went into the aravissrc pipeline
# text inside double quotes it could close.

#: A hidden camera password: the marker core/services/redact.py uses for
#: every other secret the routes hide, so a client treats them alike.
SOURCE_REDACTED = _redact.REDACTED

#: What a GigE camera name may hold. Aravis names a camera
#: "<vendor>-<model>-<serial>" or by address; this is that alphabet and no
#: quote, backslash or "!" -- the characters that end a quoted pipeline
#: property and start a new element.
_GIGE_NAME = re.compile(r"^[\w .:()+/-]{1,128}$")


class RawSourceRefused(PermissionError):
    """A camera source only the box owner may save (master)."""


def gige_name_ok(name):
    """Whether a GigE camera name is safe inside the aravissrc pipeline text."""
    return bool(_GIGE_NAME.match(str(name or "")))


def plain_source(source):
    """A camera any camera-granted account may use: a USB index, a GigE or
    depth camera by (safe) name, or a network camera URL. Everything else
    open_capture_spec would accept -- gst:, file:, a bare path, file:// and
    FFmpeg's other protocols -- is an escape hatch, not a camera: a pipeline
    can write files as this process, and a path reaches outside the system."""
    s = str(source or "").strip()
    if s.isdigit() or s in ("gige", "orbbec") or s.startswith("orbbec:"):
        return True
    if s.startswith("gige:"):
        return gige_name_ok(s.split(":", 1)[1])
    if "://" not in s:
        return False
    return s.split("://", 1)[0].lower() in ("rtsp", "rtsps", "http", "https")


def mask_source(source):
    """`source` with a URL password replaced by SOURCE_REDACTED. The rule
    itself lives in core/services/redact.py, shared with every reader that
    masks a label or a line (mask_text)."""
    return _redact.mask_source(source, SOURCE_REDACTED)


def mask_text(text):
    """A camera label or a line of text, every credential in it masked
    (redact.mask_text): what a reader applies to anything the app wrote."""
    return _redact.mask_text(text, SOURCE_REDACTED)


def unmask_source(source, stored):
    """A source a client sent back, with a hidden password restored.

    Only an EXACT round trip is restored: the stored source, masked, must be
    what came back. A masked password with a changed host is refused rather
    than carried over -- otherwise an account that was never shown the
    password could point the camera at its own server and receive it."""
    s = str(source or "")
    if SOURCE_REDACTED not in s:
        return s
    stored = str(stored or "")
    if stored and mask_source(stored) == s:
        return stored
    raise ValueError("this camera's password is hidden from your account - "
                     "type the password again to change its address")


def clean_label(label):
    """A camera label as it is SAVED: trimmed, and with any user:password@
    in it dropped rather than masked ("IP camera admin:pw@10.0.0.5" is saved
    as "IP camera 10.0.0.5"). Pages before 1.0.37 built the label from a
    pasted address, password and all, and a label is shown to everyone."""
    text = mask_text(str(label or "").strip())
    return re.sub(r"[^\s/:@'\"<>]+:" + re.escape(SOURCE_REDACTED) + "@", "", text).strip()[:80]


def mask_camera(cam):
    """A copy of one camera entry with its source's password hidden, and
    its label and name too: a label made from a pasted address ("IP camera
    admin:pw@10.0.0.5", saved by pages before 1.0.37) carries it as well."""
    if not isinstance(cam, dict):
        return cam
    out = dict(cam)
    if "source" in out:
        out["source"] = mask_source(out.get("source"))
    for key in ("label", "name", "error"):
        if isinstance(out.get(key), str):
            out[key] = mask_text(out[key])
    return out


def mask_cameras(cams):
    return [mask_camera(c) for c in (cams or [])]


def _refuse_raw(source, allow_raw, was=""):
    """Refuse saving an escape-hatch source for anyone but the box owner. A
    source that is unchanged (a master set it, a site admin renames the
    camera) is not a new decision and passes."""
    s = str(source or "").strip()
    if allow_raw or not s or plain_source(s) or s == str(was or "").strip():
        return
    raise RawSourceRefused(
        "a GStreamer pipeline, a file path or another protocol is the box "
        "owner's alone (master) - choose a USB, IP (RTSP/HTTP), GigE or "
        "depth camera")


def gst_gige_pipeline(width=None, height=None, fps=None, camera_name=None,
                      exposure_auto=None, exposure_us=None,
                      gain_auto=None, gain_db=None, binning=None):
    """A GStreamer pipeline for a GigE Vision camera through Aravis. OpenCV has
    no native GigE Vision support -- Aravis speaks the protocol and `aravissrc`
    hands frames into GStreamer, then videoconvert normalises whatever pixel
    format the camera streams (Mono8, Bayer, YUV) to BGR for OpenCV. Needs the
    aravis GStreamer plugin (libaravis-0.8-0). `camera_name` selects one when
    several are on the network.

    Exposure and gain are the two settings an industrial camera is USELESS
    without: aravissrc leaves both on "off" (whatever the camera was last set
    to, by whatever software touched it last), which is how the same camera
    gives a black picture on one board and a blown-out one on the next. Asked
    for explicitly here, so what the operator picked is what the pipeline runs
    -- and continuous is the default, because a camera that adapts to the light
    is what a person expects a camera to do.
    """
    src = "aravissrc"
    if camera_name:
        # Inside double quotes in pipeline TEXT: a name holding a quote would
        # close the property and append elements of its own (filesink...).
        if not gige_name_ok(camera_name):
            raise ValueError("that GigE camera name has characters a camera "
                             "name cannot hold")
        src += f' camera-name="{camera_name}"'
    # Both are always stated, never left to whatever the camera remembers.
    exp_auto = _gige_auto(exposure_auto)
    gain_mode = _gige_auto(gain_auto)
    src += f" exposure-auto={exp_auto} gain-auto={gain_mode}"
    # The manual value belongs to manual mode only: setting `exposure` while
    # exposure-auto is on is a value the camera immediately overwrites, and
    # reading it back later would report a setting that is not in force.
    if exp_auto == "off" and exposure_us:
        src += f" exposure={float(exposure_us):.0f}"
    if gain_mode == "off" and gain_db is not None and float(gain_db) >= 0:
        src += f" gain={float(gain_db):.2f}"
    # BINNING: the sensor adds neighbouring pixels together in hardware, so
    # 2x2 sends a quarter of the data and keeps the WHOLE field of view. That
    # is the difference from asking for a smaller size, which on a GigE camera
    # crops the sensor instead of scaling it. It is the one setting that buys
    # frame rate on a link that cannot carry the full picture -- 1.9fps at
    # 2448x1944 on a 100 Mbps link becomes about 8 at 2x2 -- and it brightens
    # the image at the same time, at the cost of detail.
    try:
        bins = int(binning or 1)
    except (TypeError, ValueError):
        bins = 1
    # 2 and 4 only: those are the modes a sensor implements, and asking for 3
    # is asking for a mode that does not exist -- the camera refuses the write
    # and the whole stream fails to negotiate, which is a strange way to lose
    # a feed to a number nobody could have picked from the page.
    if bins in (2, 4):
        src += f" h-binning={bins} v-binning={bins}"
        # The binned sensor is native/N, so a size ask written for the full
        # sensor no longer describes anything the camera can send. Dropped
        # rather than divided: what the operator asked for was a picture size,
        # and the honest binned answer is "whatever the sensor now gives",
        # scaled downstream like every other oversize frame.
        width = height = None
    caps = ""
    if width and height:
        # CLAMPED to what the camera can actually do. A size it cannot reach is
        # not a smaller picture, it is a pipeline that never negotiates: the
        # caps fail, the stream stops with "not-negotiated", and the operator
        # is told the camera "gave no frames" -- of a camera that is plugged
        # in, powered and answering. This one tops out at 2448x2048 and was
        # being asked for 2592x1944, a size it had inherited from the USB
        # camera it replaced.
        top = _gige_max_resolution(camera_name or "")
        if top:
            width, height = min(int(width), top[0]), min(int(height), top[1])
        caps = f" ! video/x-raw,width={int(width)},height={int(height)}"
        if fps:
            caps += f",framerate={int(fps)}/1"
    return (f"{src}{caps} ! videoconvert ! video/x-raw,format=BGR "
            "! appsink drop=true max-buffers=1")


def open_capture_spec(source, width=None, height=None, fps=None, tuning=None):
    """Resolve a source string to (capture_arg, cv2_backend_flag_name). Shared so
    the master's snapshot and the app's own loop open a source identically:

      "gige[:name]"      -> an Aravis GStreamer pipeline (GigE Vision cameras)
      "gst:<pipeline>"    -> a raw GStreamer pipeline (advanced escape hatch)
      "rtsp/http://…"     -> FFmpeg backend (H.264/H.265/MJPEG, codec = FFmpeg's)
      "0", "1", …         -> a V4L2 device index (USB/UVC)

    CSI ("libcamera[:name]") is NOT here: this hardware has no ribbon connector,
    so the source is refused with a message rather than silently opening
    nothing. A config carrying one predates that decision.
    """
    s = str(source).strip()
    if s == "libcamera" or s.startswith("libcamera:"):
        raise ValueError(
            "CSI (libcamera) cameras are not supported on this hardware - "
            "re-add this camera as USB, IP (RTSP) or GigE")
    if s == "gige" or s.startswith("gige:"):
        name = s.split(":", 1)[1] if ":" in s else None
        # `tuning` is the camera's own exposure/gain settings when the caller
        # has them (the app opening its configured camera). A caller that does
        # not -- a preview of a source nobody has saved yet -- gets the
        # defaults, which is continuous on both.
        t = tuning or {}
        return gst_gige_pipeline(width, height, fps, name,
                                 exposure_auto=t.get("exposure_auto"),
                                 exposure_us=t.get("exposure_us"),
                                 gain_auto=t.get("gain_auto"),
                                 gain_db=t.get("gain_db"),
                                 binning=t.get("binning")), "CAP_GSTREAMER"
    if s.startswith("gst:"):
        return s[4:], "CAP_GSTREAMER"
    # "file:<path>" -- a recorded clip or a still image, played as a camera.
    # Relative to the system's media directory, which the caller resolves
    # (media_path); an absolute path is passed through for a file kept
    # outside the system.
    if s.startswith("file:"):
        return s[5:].strip(), "CAP_FFMPEG"
    if "://" in s:
        return s, "CAP_FFMPEG"
    try:
        index = int(s)
    except (TypeError, ValueError):
        return s, "CAP_FFMPEG"                              # a file path, say
    # /dev/cam-usb<n> is a udev symlink pinned to the PHYSICAL USB socket:
    # "camera 0" opens whatever is plugged into socket 0, wherever the kernel
    # enumerated it. Resolved to the node's INTEGER index -- this OpenCV
    # build's V4L2 backend refuses a "/dev/videoN" path ("can't be used to
    # capture by name"). Mirrors the app's _capture_spec -- change together.
    link = f"/dev/cam-usb{index}"
    if os.path.islink(link):
        real = os.path.realpath(link)
        m = re.match(r"/dev/video(\d+)$", real)
        if m and os.path.exists(real):
            return int(m.group(1)), "CAP_V4L2"
    return index, None


# -- why a camera would not open ---------------------------------------------
#
# cv2 answers one bit, isOpened(), whatever went wrong, so a preview of an
# unplugged USB camera, a rejected RTSP password and a wrong stream path all
# came back as the same "cannot open camera". These ask the device again,
# directly, only after a failure, and answer with a short FACT in fixed
# wording that frontend/src/lib/camErrors.js turns into the operator's
# sentence. Mirrors the app's camera.diagnose_open -- duplicated on purpose
# (the app must not import core/); tests/smoke_camcause.py holds the two
# copies to the same answers.

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
    as open_capture_spec)."""
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


def _open_capture(source, width=None, height=None, fps=None, tuning=None):
    """Open a cv2 capture for any supported source, or raise RuntimeError."""
    try:
        import cv2                                          # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("opencv-python is not installed on this host; "
                           "cannot open or preview a camera") from e

    arg, backend = open_capture_spec(source, width, height, fps, tuning)
    cap = (cv2.VideoCapture(arg, getattr(cv2, backend)) if backend
           else cv2.VideoCapture(arg))
    gige = str(source).strip().startswith("gige")
    if not cap.isOpened() and gige:
        # At the camera's OWN size, then. The asked size is a request and the
        # sensor is a fact -- and where the two disagree the pipeline does not
        # deliver a smaller picture, it fails to negotiate at all.
        cap.release()
        arg, backend = open_capture_spec(source, None, None, None, tuning)
        cap = cv2.VideoCapture(arg, getattr(cv2, backend))
    if not cap.isOpened():
        cap.release()
        # A GigE camera has one failure worth naming: another application
        # holds its control channel, and every write is then refused. It reads
        # as "no frames" from here, which sends the operator looking at the
        # network for a camera that is answering perfectly.
        #
        # Asked with ONE discovery broadcast: no answer from any GigE camera at
        # all is a fact of its own (cable, power, the other network), and the
        # only "not found" claimed -- a camera merely missing from a list of
        # others may be listed under a name spelt differently.
        found = scan_gige() if gige else []
        if gige and not found:
            raise RuntimeError(
                f"camera {source!r}: no GigE camera answered on the network")
        if gige and _gige_controlled(source, found):
            raise RuntimeError(
                f"camera {source!r} is controlled by another application - "
                "a GigE camera has ONE controller at a time. Stop whatever is "
                "streaming it (another system on this box, or MVS/eBUS on a "
                "PC) and try again.")
        from core.services.logs import redact                 # noqa: PLC0415
        # The 409 detail lands on screen under "Preview snapshot", and an
        # RTSP source carries its password in the URL.
        cause = "" if gige else diagnose_open(source)
        if cause:
            raise RuntimeError(redact(f"cannot open camera {source!r}: {cause}"))
        raise RuntimeError(redact(
            f"cannot open camera {source!r} - it may be in use by "
            "the running detector, absent, or an unreachable URL."))
    if width and backend != "CAP_GSTREAMER":               # gst sets size in the pipeline
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
    if height and backend != "CAP_GSTREAMER":
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
    return cap


def _shared_feed_jpeg(source):
    """One JPEG of `source` taken from a running app's /raw re-serve, or None.

    The detection apps share cameras through a registry (the system app's
    camera.py): whichever app owns the device re-serves its frames on a local
    port. While an owner holds the device, opening it here simply fails -- so
    a preview must drink from the same tap. Any problem (no registry, dead
    owner, unreachable port) returns None and the caller opens the device."""
    import tempfile                                         # noqa: PLC0415
    import urllib.request                                   # noqa: PLC0415
    import hashlib                                          # noqa: PLC0415
    path = os.path.join(tempfile.gettempdir(), "cytron_cameras.json")
    # Keyed as the app's camera.reg_key keys it: a USB index as is, any
    # other source by a hash (the file is in /tmp; a raw key was the RTSP
    # password). The raw key is still read for an app from before 1.0.37.
    s = str(source)
    key = s if s.isdigit() else "h:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]
    try:
        with open(path, "r", encoding="utf-8") as f:
            reg = json.load(f) or {}
        entry = reg.get(key) or reg.get(s)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(entry, dict) or not entry.get("port"):
        return None
    if os.name != "nt":                 # no cheap pid probe on Windows; the
        try:                            # HTTP timeout below decides instead
            os.kill(int(entry.get("pid")), 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass                        # alive, just another user's process
        except (OSError, ValueError, TypeError):
            return None
    url = f"http://127.0.0.1:{int(entry['port'])}/raw"
    if entry.get("camera"):
        url += f"?camera={entry['camera']}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:   # noqa: S310
            buf = b""
            # The feed is multipart MJPEG: scan for the first complete
            # JPEG (SOI..EOI) and stop. Bounded so a misbehaving stream
            # cannot grow the buffer forever.
            while len(buf) < 8_000_000:
                chunk = r.read(4096)
                if not chunk:
                    break
                buf += chunk
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    continue
                end = buf.find(b"\xff\xd9", start + 2)
                if end > 0:
                    return buf[start:end + 2]
    except OSError:
        return None
    return None


def snapshot(source, width=None, height=None, fps=None, tuning=None):
    """One JPEG frame from `source`, for a preview before committing to it.

    If a running app already owns the source (this system's or another's),
    the frame comes from its shared /raw feed -- the device itself only has
    one reader. Otherwise opens the source, grabs a frame, releases
    immediately -- never holds the device, because the running detector
    needs it."""
    shared = _shared_feed_jpeg(source)
    if shared:
        return shared
    # _open_capture imports cv2 and raises RuntimeError if it is missing, so open
    # FIRST -- then cv2 is guaranteed importable for the encode below, and a
    # missing-opencv host answers a clean error instead of a 500.
    cap = _open_capture(source, width, height, fps, tuning)
    import cv2                                              # noqa: PLC0415
    try:
        ok = frame = None
        for _ in range(5):              # first frames off a fresh capture can be empty
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if not ok or frame is None:
            raise RuntimeError("camera opened but produced no frame")
        okj, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not okj:
            raise RuntimeError("could not encode the frame to JPEG")
        return buf.tobytes()
    finally:
        cap.release()


#: Detector kinds that actually run a chosen MODEL, and so have a name worth
#: reporting. A mock system must not display the template's yolov8n as though
#: it were selected. MediaPipe belongs here: it names which of its models is
#: running ("hands"), even though the weights ship with the wheel.
MODEL_KINDS = ("hailo", "gstreamer", "mediapipe")


#: pkg_dir -> (when, last non-empty runtime state). See the grace note in
#: status(): an empty read moments after a live one is a transient, and
#: reporting it as truth is what made System Control rows blink.
_running_cache = {}
RUNNING_GRACE_S = 10.0


def _merge_live_cameras(cams, live_list):
    """Fold the app's per-camera runtime (error, measured fps, and the
    {"feed", "why"} view when [video] feed asks for passthrough) into the
    config-derived camera list, matched by id. Extra runtime entries (a
    camera just removed from config) are ignored; missing ones leave the
    config entry untouched."""
    live = {str(c.get("id")): c for c in (live_list or []) if isinstance(c, dict)}
    for c in cams:
        lv = live.get(str(c.get("id")))
        if lv is not None:
            c.update(error=lv.get("error"), measured_fps=lv.get("measured_fps"),
                     **({"view": {k: str(lv["view"].get(k) or "")[:120] for k in ("feed", "why")}}
                        if isinstance(lv.get("view"), dict) else {}))
    return cams


def ai_flag(cfg):
    """The AI detection switch, [ai] on, in a merged config. No table = ON,
    exactly as the app reads it (app/run.py ai_on)."""
    ai = cfg.get("ai") if isinstance((cfg or {}).get("ai"), dict) else {}
    return ai.get("on") is not False


def set_ai(systems_dir, slug, on):
    """Write the AI detection switch. A running app follows it on its next
    config poll (~2 s): camera and detector opened or released IN PLACE,
    every other rule left running (app/run.py ai_config)."""
    with edit_local(_pkg_dir(systems_dir, slug)) as local:
        local["ai"] = {"on": bool(on), "at": int(time.time())}


def _ai_split(st, cfg):
    """(app alive, AI detection on) from a runtime status and the config.

    The switch is the config's word, not the app's report: Stop answers
    "stopped" the moment it is pressed and Start is counted against the
    budgets at once, while the app catches up within a poll. An app from
    before the switch says what it is by its mode: "logic" never detects,
    and a detection app without the unified mode always does."""
    st = st or {}
    alive = bool(st.get("running"))
    if not alive:
        return False, False
    mode = st.get("mode")
    if mode == "logic":
        return True, False
    if mode == "unified":
        return True, ai_flag(cfg or {})
    return True, True


#: Mirrors the app's runtime.AI_OK_FRESH_S: past this without a heartbeat
#: the app is not turning frames over, so the dot cannot be green. Checked
#: here as well because an older app copy passes its last ai_ok straight
#: through.
AI_OK_FRESH_S = 10


def _heartbeat_age(st):
    try:
        return time.time() - float((st or {}).get("last_heartbeat") or 0)
    except (TypeError, ValueError):
        return float("inf")


def _ai_ok(st, run_alive):
    """Green dot: detection is on AND the app says it is producing, on a
    fresh heartbeat -- a stalled app is not producing anything."""
    st = st or {}
    if not run_alive:
        return False
    if st.get("stalled") or _heartbeat_age(st) >= AI_OK_FRESH_S:
        return False
    if st.get("mode") != "unified":
        # An app from before the dot: running with no live error is the best
        # it can say.
        return not st.get("error")
    return bool(st.get("ai_ok")) and st.get("ai_on") is not False


def _ai_reason(st, app_alive, run_alive):
    """The red dot's tooltip, or "" when it is green."""
    st = st or {}
    if not app_alive:
        return "the system's app is not running"
    if not run_alive:
        return "AI detection is off"
    if _ai_ok(st, run_alive):
        return ""
    age = _heartbeat_age(st)
    if st.get("stalled") or age >= AI_OK_FRESH_S:
        return ("the app is not responding" + (
            f" (no heartbeat for {int(age)} s)" if age != float("inf") else ""))
    if st.get("mode") == "unified" and st.get("ai_on") is False:
        return "AI detection is starting"
    return str(st.get("ai_error") or st.get("error")
               or "AI detection is starting")


def status(systems_dir, slug, reveal=False):
    """Everything the Detection page needs to render: the current camera,
    detector/model, zones, and whether the app process is actually running.

    Camera passwords are MASKED unless `reveal` (Pipi H2, 2026-10-11): this
    dict reaches /api/dashboard and /api/overview/live for every signed-in
    account, and a plain `user` read the IP camera's admin password there.
    Only /api/detector/control reveals, and only to an account holding
    detection.camera.change, whose edit form keeps saved credentials by
    rebuilding them from the source it was given. Error texts are masked
    too: a state.json from an older app may still quote an address."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    cfg = _merged(pkg_dir)
    camera = cfg.get("camera") or {}
    detector = cfg.get("detector") or {}
    app = cfg.get("app") or {}

    running = {}
    rt = _app_runtime(pkg_dir)
    if rt is not None:
        try:
            # pkg_dir lets the runtime prove the recorded pid really is THIS
            # system's app before trusting it (see runtime._owns).
            try:
                running = rt.status(_data_dir(pkg_dir, cfg), pkg_dir)
            except TypeError:
                # A created system running app code from before the second
                # argument existed. It refreshes at the next hub boot; until
                # then the old signature still answers.
                running = rt.status(_data_dir(pkg_dir, cfg))
        except Exception:               # noqa: BLE001
            running = {}
    # A read that comes back EMPTY right after one that had a live pid is a
    # transient (state.json mid-rotation, a busy SD card), not a crash -- a
    # process does not vanish without its state file noticing. Reuse the last
    # good answer briefly; a real stop still shows within the grace window,
    # and a real crash is caught by the app's own heartbeat staleness.
    now = time.time()
    prev = _running_cache.get(pkg_dir)
    if not running and prev and now - prev[0] < RUNNING_GRACE_S:
        running = prev[1]
    elif running:
        _running_cache[pkg_dir] = (now, running)

    # A stopped system must not carry a LIVE fault. runtime.status() already
    # splits the two, but every installed system runs its OWN copy of the app
    # under the store, so a system created before that split still hands back
    # the last run's reason as `error`. Re-apply the gate here, where the hub
    # code is always current: live errors only while the app is alive, and the
    # leftover reason reported as history with the moment it was written.
    # ONE app per system, AI detection a switch inside it (_ai_split). Every
    # page that asks "is detection running" gets the switch's answer, and
    # `logic_running` says the workflow is up with the AI off.
    app_alive, run_alive = _ai_split(running, cfg)
    logic_alive = app_alive and not run_alive
    last_error = running.get("last_error")
    if not run_alive and last_error is None:
        last_error = running.get("error")

    st = {
        "running": run_alive,
        "logic_running": logic_alive,
        # The app process itself, whatever the AI switch says.
        "app_running": app_alive,
        # The "AI running" dot: green only while a real detector and a camera
        # are producing; otherwise `ai_error` says why (off, failed, loading).
        "ai_ok": _ai_ok(running, run_alive),
        "ai_error": _ai_reason(running, app_alive, run_alive),
        # Stopped by the BOX: "camera_missing" (app/camwatch.py), else None.
        "ai_paused": (running.get("ai_paused") or None) if run_alive else None,
        # Alive but not turning frames over -- loading a model, or reopening a
        # camera that dropped. Separate from `running` because "busy" and
        # "stopped" want different words on screen and different buttons.
        "stalled": bool(running.get("stalled")),
        # The switch is OFF but the app has not released the camera and the
        # chip yet (core/services/power_state.py). The pages hold Start on
        # it; start() refuses on it (409); class_lock reads it as busy.
        "stopping": power_state.ai_stopping(running, app_alive, run_alive,
                                            _heartbeat_age(running),
                                            AI_OK_FRESH_S),
        "pid": running.get("pid"),
        "uptime_s": running.get("uptime_s") or 0,
        "detector_name": running.get("detector"),
        "fps": running.get("fps"),
        "infer_fps": running.get("infer_fps") or {},
        "error": running.get("error") if run_alive else None,
        # The last run's outcome, for a quiet "how it ended" line. Never an
        # alarm: nothing is failing on a system that is not running.
        "last_error": last_error,
        "last_error_at": running.get("last_error_at"),
        # Every camera this system captures. The singular block below stays as
        # the FIRST one so the hub table, the fleet adapter and every existing
        # consumer keep reading a camera rather than nothing. Names refreshed
        # against the hardware actually present (see _refresh_usb_names) --
        # this is the payload the Detection page's header reads, so a swapped
        # camera must stop introducing itself as the old one HERE too.
        # ...with each camera's LIVE runtime merged in by id (error, measured
        # fps): only the first camera's error reaches the top-level `error`,
        # so with several cameras the page showed a healthy synthetic feed
        # for a camera the app had actually given up on.
        "cameras": _merge_live_cameras(_refresh_usb_names(_camera_list(cfg)),
                                       running.get("cameras")),
        "camera": {
            "source": str(camera.get("source", "")),
            # The name the operator picked, kept beside the machine spec so the
            # UI never has to re-derive it from a device index. Written once by
            # set_camera; it cannot drift because nothing recomputes it.
            "label": str(camera.get("label") or ""),
            "width": int(camera.get("width") or 640),
            "height": int(camera.get("height") or 480),
            "fps": int(camera.get("fps") or 10),
        },
        "detector": {
            "kind": detector.get("kind") or "mock",
            # hailo sub-table first -- it is what the app actually runs (the
            # detector factory merges it over the top level). A model is only
            # reported for kinds that consume one, so a fresh mock system does
            # not display the template's yolov8n as if it were selected.
            # With a [[detector.models]] list the MULTI list is the truth (the
            # factory ignores the single keys then); "model" becomes a joined
            # display string so every existing consumer still shows something
            # sensible.
            "model": (_model_display(detector)
                      if (detector.get("kind") or "mock") in MODEL_KINDS
                      else None),
            "models": (_model_entries(detector)
                       if (detector.get("kind") or "mock") in MODEL_KINDS
                       else []),
            # WHERE the model came from. Two folders can hold HEFs with the
            # same filename, so the page needs the directory to know which
            # entry is the running one.
            "models_dir": ((detector.get("hailo") or {}).get("models_dir")
                           or detector.get("models_dir") or ""),
            "task": detector.get("task") or "detection",
            "confidence": float(detector.get("confidence") or 0.45),
            "classes": list((detector.get("hailo") or {}).get("classes")
                            or detector.get("classes") or []),
        },
        "zones": _zone_list(cfg),
        "stream_port": int(app.get("stream_port") or DEFAULT_STREAM_PORT),
        "template": app.get("template") or "blank",
        "zone_guidance": _zone_guidance(app.get("template") or "blank"),
    }
    if reveal:
        return st
    st.update(camera=mask_camera(st.get("camera")), cameras=mask_cameras(st.get("cameras")))
    for key in ("error", "last_error", "ai_error"):
        if isinstance(st.get(key), str):
            st[key] = mask_text(st[key])
    return st


#: pkg_dir -> (when, data_dir). Resolving it costs two TOML reads and a call
#: into the app's own config module. It cannot change without a config edit and
#: a restart, so the footer's two-second poll looks it up once a minute.
_data_dir_cache = {}
DATA_DIR_TTL_S = 60.0


def _data_dir_cached(pkg_dir):
    hit = _data_dir_cache.get(pkg_dir)
    now = time.time()
    if hit is not None and now - hit[0] < DATA_DIR_TTL_S:
        return hit[1]
    path = _data_dir(pkg_dir, _merged(pkg_dir))
    _data_dir_cache[pkg_dir] = (now, path)
    return path


def data_dir(systems_dir, slug):
    """Where a system's app keeps its data (events, snapshots, the log spool).

    Public because other services need the ANSWER and must not re-derive the
    rule: it already went wrong once, resolving to the eMMC while the app
    wrote to the NVMe, and a caller looking in the wrong place finds an empty
    folder rather than an error.
    """
    return _data_dir_cached(_pkg_dir(systems_dir, slug))


def fps_snapshot(systems_dir, slug):
    """{running, fps} for the hardware footer -- the two things status() knows
    that a two-second poll can afford to ask for.

    status() is the full picture: cameras, detector, zones, model list. Getting
    there costs two TOML reads per call AND a liveness probe that, on a Windows
    dev box, SHELLS OUT to tasklist. This reads the app's state file and judges
    life by its heartbeat instead -- the same staleness rule status() applies,
    minus the pid check, which cannot disagree with a heartbeat that is younger
    than the app's own STALE_AFTER_S.

    fps is None, never 0, when there is nothing running to have an fps: a
    stopped detector and a detector managing zero frames a second are different
    problems and must not render the same.
    """
    out = {"running": False, "fps": None}
    pkg_dir = _pkg_dir(systems_dir, slug)
    rt = _app_runtime(pkg_dir)
    if rt is None or not hasattr(rt, "read"):
        return out
    try:
        state = rt.read(_data_dir_cached(pkg_dir)) or {}
    except Exception:                   # noqa: BLE001
        return out
    stale_after = float(getattr(rt, "STALE_AFTER_S", 30) or 30)
    fresh = (time.time() - float(state.get("heartbeat") or 0)) < stale_after
    # The app with the AI switched off is not AI running (see status()).
    out["running"] = bool(state.get("pid")) and fresh \
        and state.get("mode") != "logic" \
        and not (state.get("mode") == "unified"
                 and state.get("ai_on") is False)
    fps = state.get("fps")
    if out["running"] and isinstance(fps, (int, float)):
        out["fps"] = round(float(fps), 1)
    return out


def _model_entries(detector):
    """The [[detector.models]] list as plain dicts (empty when single-model)."""
    out = []
    for e in detector.get("models") or []:
        if isinstance(e, dict) and e.get("model"):
            out.append({"model": str(e["model"]),
                        "classes": [str(c) for c in (e.get("classes") or [])],
                        "confidence": (float(e["confidence"])
                                       if e.get("confidence") else None)})
    return out


def _model_display(detector):
    """What the UI calls "the model": the multi list joined, else the single
    key (hailo sub-table first -- it is what the app actually runs)."""
    entries = _model_entries(detector)
    if entries:
        return " + ".join(e["model"] for e in entries)
    return ((detector.get("hailo") or {}).get("model")
            or detector.get("model"))


def _zone_list(cfg):
    # Which camera a zone belongs to. Points are normalised, so without this a
    # shape drawn over one camera's picture would silently apply to every other
    # camera too. Zones written before multi-camera carry no id and belong to
    # the first camera, which is the only one that existed when they were drawn.
    default_cam = (_camera_list(cfg) or [{"id": MAIN_CAMERA}])[0]["id"]
    out = []
    for z in cfg.get("zones") or []:
        entry = {
            "id": str(z.get("id") or z.get("name") or "zone"),
            "name": str(z.get("name") or z.get("id") or "zone"),
            "camera": _camera_id(z.get("camera"), "") or default_cam,
            "kind": (z.get("kind") or "area").lower(),
            "points": [[float(x), float(y)] for x, y in (z.get("points") or [])],
            "violation": bool(z.get("violation", False)),
            "enabled": bool(z.get("enabled", True)),
            "match": str(z.get("match") or ""),
            "color": str(z.get("color") or ""),
            "labels": [str(l) for l in (z.get("labels") or [])],
            "actions": [dict(a) for a in (z.get("actions") or [])
                        if isinstance(a, dict)],
        }
        # This list is what the Zones page loads and POSTs back whole on its
        # next save, so a key missing here is a key that save deletes: a line
        # saved as "none" or "reverse" was reset to forward, and a depth range
        # vanished, the first time anybody edited an unrelated zone. Copied
        # through only when present, so a zone without them lists exactly the
        # keys it always did.
        for key in ("facing", "depth_min_mm", "depth_max_mm", "band_pct"):
            if z.get(key) not in (None, ""):
                entry[key] = z[key]
        out.append(entry)
    return out


#: How many cameras this BOX may RUN at once, across every system together.
#: The cap is on ENABLED cameras, not configured ones: every running camera
#: costs CPU (a grab thread, a decode, a JPEG encode per frame) and past four
#: the Pi's cores, not the accelerator, run out. A DISABLED camera costs
#: nothing, so any number may be configured and switched between -- four night
#: cameras and four day cameras on one box is a normal setup.
MAX_CAMERAS = 4

#: One system may use the whole running budget -- the box cap is what
#: protects the neighbours.
MAX_CAMERAS_PER_SYSTEM = 4

#: How many adjusted controls one camera may carry. A bound on what ends up in
#: config.local.toml rather than on what a camera has -- no device reports
#: anything like this many, and the file is re-read by the app and parsed by
#: the hub, so unbounded growth there is everyone's problem.
MAX_SAVED_CONTROLS = 32

#: How many camera ENTRIES one system may keep, running or not. A sanity
#: bound, not a resource one -- a list longer than this is a config mistake.
MAX_CONFIGURED_CAMERAS = 16

#: How a camera reaches this box. Persisted per entry rather than re-derived,
#: because the operator PICKED it (the mode buttons on the camera panel) and a
#: source string is a lossy record of that choice.
#: "synthetic" is NOT one: AI vision needs a camera, and a generated picture
#: standing in for one is a system that looks like it is watching something.
#: "unset" is the honest state of a system nobody has pointed at hardware
#: yet -- it exists so an unconfigured camera can be described rather than
#: mislabelled, and start() refuses a system that has only those.
CAMERA_KINDS = ("usb", "rtsp", "gige", "file", "depth", "unset")

#: Said when a customer box is asked for a depth camera. Named in the words
#: of the panel, with what IS offered, so the refusal is also the way on.
DEPTH_UNAVAILABLE = ("depth (3D) cameras are not available on this box - "
                     "choose a USB, IP (RTSP), GigE or video-file source")


def depth_offered(config_dir=None):
    """Are depth (3D) cameras offered on this box? Never.

    Depth is R&D and is not part of the shipped product: no Depth tab, no
    depth camera in discovery, and a new one is refused. (This used to be
    true only on a developer box; the developer tier is gone, so it is now
    unconditionally off. Kept as a function so every caller still asks.)

    What this does NOT do is touch a depth camera already stored in a
    customer box's config: none exists in the field, and silently switching
    one off would be a change nobody asked for. The gate is on the way in.
    """
    return False

#: The running cap PER KIND, on top of the box and per-system caps above.
#: MAX_CAMERAS is about the box's total CPU; these are about the bus each kind
#: arrives on. A USB or GigE camera hands this box RAW frames -- the CPU does
#: the whole capture, and two of either is where a Pi starts dropping them. An
#: IP camera encodes on its own silicon and sends H.264, so the box only
#: decodes: far cheaper per camera, and the box-wide four is the real ceiling.
#: A file source touches no capture hardware at all.
#: "depth" counts with USB because that is the bus it loads: an Orbbec
#: Gemini sends colour AND two IR streams up one USB3 cable, so one of
#: them is at least as much capture work as a UVC camera.
KIND_LIMITS = {"usb": 2, "gige": 2, "rtsp": 4, "file": 4, "depth": 1,
               "unset": 16}

#: What each kind is called when refusing one, so the message names the thing
#: the operator sees on the panel rather than the config token.
KIND_LABELS = {"usb": "USB", "gige": "GigE", "rtsp": "IP (RTSP)",
               "file": "file", "depth": "depth", "unset": "not set up"}


def camera_kind(source, declared=""):
    """Which bus a camera arrives on: 'usb', 'rtsp', 'gige', 'file',
    'depth', or 'unset' when no source has been chosen yet.

    The DECLARED kind wins -- it is what the operator chose on the panel. It is
    only inferred when absent, which is every camera configured before this
    field existed and every hand-written config.local.toml. Inferring rather
    than exempting matters: the per-kind caps below are a hardware limit, and a
    config that predates the field must not be a way around them.

    The inference mirrors open_capture_spec()'s dispatch exactly -- same
    strings, same order -- because the thing being classified is the same thing
    that function is about to open.
    """
    d = str(declared or "").strip().lower()
    # A config written before synthetic was withdrawn still carries the word,
    # in `kind` or in `source`. It reads as UNSET rather than being honoured:
    # the system then refuses to start and asks for a real camera, which is
    # the whole point -- upgrading a box must not leave a system quietly
    # running a generated picture.
    if d == "synthetic":
        return "unset"
    if d in CAMERA_KINDS:
        return d
    s = str(source or "").strip()
    if not s or s == "synthetic":
        return "unset"
    if s == "gige" or s.startswith("gige:"):
        return "gige"
    # A depth camera is on USB, but it is not a UVC camera: it opens through
    # its own SDK, offers its own controls, and publishes a distance per pixel
    # as well as a picture. Named apart so the panel can say so.
    if s == "orbbec" or s.startswith("orbbec:"):
        return "depth"
    if s.startswith("gst:"):
        return "file"                  # a hand-written pipeline: no known bus
    if "://" in s:
        return "rtsp"
    try:
        int(s)
    except (TypeError, ValueError):
        return "file"
    return "usb"


def _kind_counts(entries):
    """Running cameras per kind, for a list of raw or validated entries."""
    out = {}
    for e in entries:
        if not isinstance(e, dict) or not e.get("enabled", True):
            continue
        k = camera_kind(e.get("source"), e.get("kind"))
        out[k] = out.get(k, 0) + 1
    return out

#: The slowest a camera may be asked to run. Below about this a moving object
#: jumps far enough between frames that the tracker loses it, and every count
#: built on track identity (crossings, dwell, unique visitors) starts lying
#: rather than merely lagging. The pacing floor is enforced in the app engine
#: too -- a chip share that worked out lower is raised to it.
MIN_CAMERA_FPS = 10

#: The id the single legacy [camera] table becomes. Existing zones, snapshots
#: and dashboards were all written when a system had exactly one camera, so
#: that camera has to keep its identity rather than be renumbered.
MAIN_CAMERA = "main"


def _camera_id(value, fallback="cam"):
    slug = "".join(c if c.isalnum() else "_"
                   for c in str(value or "").strip().lower()).strip("_")
    return (slug or fallback)[:32]


def _camera_list(cfg):
    """Every camera this system captures, newest config shape or oldest.

    [[cameras]] is the truth when present; a lone [camera] table is read as one
    entry called `main`. Reading BOTH shapes here (rather than migrating the
    file on read) means a system configured by hand, or by an older build, keeps
    working untouched -- and the writer below only ever emits the list form.
    """
    out, seen = [], set()
    entries = cfg.get("cameras")
    if not isinstance(entries, list):
        legacy = cfg.get("camera")
        entries = [dict(legacy, id=MAIN_CAMERA)] if isinstance(legacy, dict) and legacy else []
    # An EXPLICIT empty list is a decision, not an old config: the operator
    # removed every camera (a blank-canvas system). Falling back to the
    # legacy [camera] table here would resurrect the row they just deleted --
    # the scaffold's base config carries that table forever.
    elif not entries:
        entries = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        cid = _camera_id(e.get("id"), f"cam{i + 1}")
        while cid in seen:                      # ids address zones and files
            cid = f"{cid}_2"
        seen.add(cid)
        out.append({
            "id": cid,
            "name": str(e.get("name") or e.get("label") or "").strip()[:80],
            "source": str(e.get("source", "")),
            "label": str(e.get("label") or "").strip()[:80],
            "width": int(e.get("width") or 640),
            "height": int(e.get("height") or 480),
            "fps": int(e.get("fps") or 10),
            # Always answered, inferred when the entry predates the field, so
            # the page never has to guess what a camera is.
            "kind": camera_kind(e.get("source"), e.get("kind")),
            "enabled": bool(e.get("enabled", True)),
        })
        # The two tables an operator builds up over time on a camera: the
        # picture adjustments, and the measurement setup. Carried through --
        # cleaned, not trusted -- because this list is what every writer reads
        # before it writes, and a key missing HERE is silently deleted on the
        # next save of anything else. That is how a saved brightness and a
        # calibrated floor both vanished the first time a profile was saved.
        controls = e.get("controls")
        if isinstance(controls, dict):
            clean = {str(k): int(v) for k, v in controls.items()
                     if re.fullmatch(r"[a-z0-9_]{1,64}", str(k))
                     and isinstance(v, (int, float)) and not isinstance(v, bool)}
            if clean:
                out[-1]["controls"] = dict(sorted(clean.items())[:MAX_SAVED_CONTROLS])
        measure_cfg = e.get("measure")
        if out[-1]["kind"] == "depth" and isinstance(measure_cfg, dict):
            clean = _clean_measure(measure_cfg)
            if clean:
                out[-1]["measure"] = clean
        # ...and the third: what the picture goes through before the detector
        # sees it. Same reason as the two above -- the workflow compiler
        # writes it, the Cameras page rewrites this whole list on every save,
        # and a key missing here is a chain deleted by somebody renaming a
        # camera.
        chain = _clean_preprocess(e.get("preprocess"))
        if chain:
            out[-1]["preprocess"] = chain
            out[-1]["preprocess_preview"] = bool(e.get("preprocess_preview"))
        # ...and the fourth: which published value starts a snapshot on a
        # camera whose Detection Stream is set to Snapshot (set_snapshot). It
        # MUST be carried here: this list is rebuilt on every camera write,
        # including set_snapshot's own, and a key missing from it is a setting
        # deleted at the moment it is saved.
        snap = _clean_snapshot(e.get("snapshot"))
        if snap:
            out[-1]["snapshot"] = snap
    return out


def cameras(systems_dir, slug):
    """This system's camera list (never empty in practice: a system without a
    camera reads as none, and the page offers to add one)."""
    cams = _camera_list(_merged(_pkg_dir(systems_dir, slug)))
    # A USB entry's name was stamped from the device present when it was
    # APPLIED. Swap the physical camera on the same index and the page keeps
    # introducing the old hardware -- "Arducam" over a feed coming from a
    # different make. So a stored name that IS the old scan label (never one
    # the operator typed) is refreshed from the device actually there now.
    return _refresh_usb_names(cams)


@locked
def set_camera_controls(systems_dir, slug, camera_id, values, config_dir=None):
    """Save what the operator adjusted on ONE camera's settings panel.

    A merge, not a replace: the panel sends the row that moved, and the others
    keep what they were set to. Clearing one back to the camera's own default
    is sending it as null, which drops it from the stored table rather than
    writing the default down as a choice -- there is a difference between "I
    want 64" and "whatever the camera does", and only one of them should
    survive a firmware update that changes the default.

    Whole-list write underneath, because that is the only shape config
    merging supports (see set_cameras).
    """
    entries = [dict(c) for c in cameras(systems_dir, slug)]
    wanted = str(camera_id or "").strip()
    target = next((e for e in entries if e.get("id") == wanted), None)
    if target is None:
        raise ValueError("no camera {!r} on this system".format(wanted))
    merged = dict(target.get("controls") or {})
    for key, value in (values or {}).items():
        if value is None:
            merged.pop(str(key), None)
        else:
            merged[str(key)] = value
    target["controls"] = merged
    saved = set_cameras(systems_dir, slug, entries, config_dir=config_dir)
    return next((c for c in saved if c.get("id") == wanted), target)


@locked
def set_camera_measure(systems_dir, slug, camera_id, patch, config_dir=None):
    """Save the measurement setup of ONE camera: its floor plane, its
    profiles, or both. A merge at the top level -- calibrating the floor
    does not forget the profiles and vice versa -- and a whole-list write
    underneath, because that is the only shape config merging supports."""
    entries = [dict(c) for c in cameras(systems_dir, slug)]
    wanted = str(camera_id or "").strip()
    target = next((e for e in entries if e.get("id") == wanted), None)
    if target is None:
        raise ValueError("no camera {!r} on this system".format(wanted))
    if target.get("kind") != "depth":
        raise ValueError("only a depth camera can measure - this one is "
                         + KIND_LABELS.get(target.get("kind"), "not a depth camera"))
    merged = dict(target.get("measure") or {})
    for key, value in (patch or {}).items():
        if value is None:
            merged.pop(str(key), None)
        else:
            merged[str(key)] = value
    target["measure"] = merged
    saved = set_cameras(systems_dir, slug, entries, config_dir=config_dir)
    return next((c for c in saved if c.get("id") == wanted), target)


def _refresh_usb_names(cams):
    """Re-point stale USB device names at the hardware actually present.

    Only a name equal to the entry's own stored `label` (or empty) is
    touched -- that equality is the proof the SCAN named it, not a person.
    An operator's "Front gate" survives any amount of replugging. Display
    only: nothing is written back, so a briefly unplugged camera does not
    get its config rewritten.
    """
    usb = [c for c in cams if c.get("kind") == "usb"]
    if not usb:
        return cams
    try:
        present = {c["index"]: c["name"] for c in list_cameras()
                   if c.get("index") is not None}
    except Exception:                                        # noqa: BLE001
        return cams
    for cam in usb:
        try:
            index = int(str(cam.get("source")))
        except (TypeError, ValueError):
            continue
        actual = str(present.get(index) or "").strip()
        stored = str(cam.get("name") or "")
        # The scan's name, not a person's: equal to the stored label, or
        # shaped like a device string -- "Arducam B0495 (USB3 2.3MP)" carries
        # its interface spec in parentheses the way no operator names a
        # camera. "Front gate" fails both tests and survives.
        scanish = (not stored or stored == cam.get("label")
                   or bool(re.search(r"\((?:usb|uvc|hd|[0-9.]+\s*mp)", stored,
                                     re.IGNORECASE))
                   or bool(re.search(r"(webcam|camera|cam)\b", stored,
                                     re.IGNORECASE)))
        if actual and actual != stored and scanish:
            cam["name"] = actual[:80]
            cam["label"] = actual[:80]
    return cams


def _enabled_source_list(cams):
    """Every device spec a camera list actually captures, ONE PER FEED.

    Duplicates kept on purpose: two enabled entries on the same device are two
    grabs, two inferences and two streams, and the box cap counts feeds. See
    sources_elsewhere.
    """
    return [str(c.get("source") or "").strip() for c in cams
            if c.get("enabled", True) and str(c.get("source") or "").strip()
            and str(c.get("source")).strip() != "synthetic"]


def _enabled_sources(cams):
    """The DISTINCT device specs a camera list actually captures.

    For asking WHICH devices, never how many -- the budget counts feeds.
    """
    return set(_enabled_source_list(cams))


def ai_running(systems_dir, slug):
    """Whether this system's LOCAL detection app is running right now.

    The one-bit answer the master's /api/health carries to the hub: most
    deployed systems pair no device agents at all -- the AI runs in the
    master's own detection app -- so without this bit the hub's card pill
    reads "idle" while detection is visibly running."""
    try:
        pkg_dir = _pkg_dir(systems_dir, slug)
        return _running_now(pkg_dir, _merged(pkg_dir))
    except Exception:                                          # noqa: BLE001
        return False


def detector_error(systems_dir, slug):
    """Why this system is running but seeing nothing, or ''.

    The detection app does not stop when its detector cannot load: it falls
    back to one that returns no detections "so the stream stays up", records
    the reason in its runtime state, and carries on reporting itself running.
    That is a defensible choice -- a dead video feed helps nobody -- but the
    reason it records reached no page, so a board whose accelerator runtime
    went missing looked exactly like a board detecting nothing of interest.
    Video flowing, ai_running true, every rule reading zero, no error
    anywhere. It took running the app by hand to find out.

    So the one bit the hub already carries gains the sentence beside it.
    """
    try:
        pkg_dir = _pkg_dir(systems_dir, slug)
        rt = _app_runtime(pkg_dir)
        if rt is None:
            return ""
        cfg = _merged(pkg_dir)
        try:
            st = rt.status(_data_dir(pkg_dir, cfg), pkg_dir)
        except TypeError:
            st = rt.status(_data_dir(pkg_dir, cfg))
        return str((st or {}).get("error") or "")
    except Exception:                                          # noqa: BLE001
        return ""


def system_config(systems_dir, slug):
    """A system's merged config (stock + local), or {}.

    The COMPILED truth about a system: what its workflow actually became, as
    the app reads it. Used to answer "is anything bound to this board
    terminal" without opening anybody's canvas.
    """
    try:
        return _merged(_pkg_dir(systems_dir, slug)) or {}
    except Exception:                                          # noqa: BLE001
        return {}


def app_state(systems_dir, slug):
    """A system's RAW app state file, or {}.

    status() answers a curated shape for the Detection page; this is the file
    as the app wrote it, for facts the app publishes that no other process can
    observe -- which board terminals it holds being the one that needed it
    (see core/services/board_io.port_owners).
    """
    try:
        pkg_dir = _pkg_dir(systems_dir, slug)
        rt = _app_runtime(pkg_dir)
        if rt is None:
            return {}
        return rt.read(_data_dir(pkg_dir, _merged(pkg_dir))) or {}
    except Exception:                                          # noqa: BLE001
        return {}


def _running_now(pkg_dir, cfg):
    """Whether this package's detection app is running RIGHT NOW."""
    rt = _app_runtime(pkg_dir)
    if rt is None:
        return False
    try:
        try:
            st = rt.status(_data_dir(pkg_dir, cfg), pkg_dir)
        except TypeError:
            st = rt.status(_data_dir(pkg_dir, cfg))
        # An app with the AI off holds no camera and no chip, so it spends
        # none of either budget.
        return _ai_split(st, cfg)[1]
    except Exception:                                          # noqa: BLE001
        return False


def sources_elsewhere(systems_dir, slug, config_dir=None):
    """The camera FEEDS the other systems on this box RUN, as a list.

    Feeds, not distinct devices. This used to return a set, on the argument
    that the capture cost is per physical device -- one grab thread and one
    decode, however many systems consume it. That is true of the CAPTURE and
    of nothing else: each system that uses a camera runs its own inference on
    it, encodes its own H.264 for the gateway and serves its own MJPEG. Two
    systems on one camera is two of everything the cap exists to limit, and
    deduplicating them let a four-camera box run six and seven video feeds at
    once -- which is what the cap was there to prevent.

    It is also only half true of capture: the sharing story assumes the second
    system points at the owner's /raw feed, which is a DIFFERENT source string
    and so was never deduplicated anyway. The systems this merged were the
    ones aimed at the same device directly -- and a raw V4L2 camera has one
    reader, so the second one does not share anything, it fails.

    Still a LIST rather than a count so callers can see the sources
    themselves; `len()` is the budget number. Duplicates within one system are
    kept for the same reason: two entries on one device are two feeds.

    RUN means run: only systems whose detection app is actually running
    spend the budget. A stopped system's configured cameras cost the box
    nothing -- counting them showed "4 out of 4" on a box running two, and
    blocked adds for capacity nobody was using.

    A package that cannot be read counts as nothing rather than blocking the
    add -- refusing a camera because some unrelated system has a broken config
    would be the wrong failure.
    """
    from core.services import provision as provision_service   # noqa: PLC0415
    out = []
    try:
        found = provision_service.system_slugs(systems_dir, config_dir)
    except Exception:                                          # noqa: BLE001
        return out
    for other, pkg in found.items():
        if other == slug:
            continue
        try:
            cfg = _merged(pkg)
            if not _running_now(pkg, cfg):
                continue
            out += _enabled_source_list(_camera_list(cfg))
        except Exception:                                      # noqa: BLE001
            continue
    return out


# Reading the other systems means parsing every package's config.toml AND
# config.local.toml, and the camera pages ask on every GET, add, enable and
# remove. A few seconds of staleness is invisible in a budget display, so the
# READ path caches. The WRITE path (the cap enforcement in set_cameras) never
# uses this: refusing or allowing a camera must be decided on what is true now.
_ELSEWHERE_TTL_S = 4.0
_elsewhere_cache = {}          # (systems_dir, slug, config_dir) -> (deadline, set)


def _sources_elsewhere_cached(systems_dir, slug, config_dir=None):
    key = (str(systems_dir), str(slug), str(config_dir))
    hit = _elsewhere_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    found = sources_elsewhere(systems_dir, slug, config_dir)
    _elsewhere_cache[key] = (now + _ELSEWHERE_TTL_S, found)
    return found


def _forget_elsewhere():
    """Called after any camera write -- this system's own change must show up
    in its next budget read without waiting out the TTL."""
    _elsewhere_cache.clear()


def camera_budget(systems_dir, slug, config_dir=None):
    """What this system may still add, and which limit is stopping it.

    TWO limits apply: this system's own (MAX_CAMERAS_PER_SYSTEM) and the box's
    shared accelerator budget (MAX_CAMERAS). `limit` names whichever is binding
    so the page can say WHY rather than just greying a button out.
    """
    mine = cameras(systems_dir, slug)
    here = sum(1 for c in mine if c["enabled"])
    elsewhere = len(_sources_elsewhere_cached(systems_dir, slug, config_dir))
    by_system = MAX_CAMERAS_PER_SYSTEM - here
    # FEEDS box-wide, not distinct devices. Re-using a camera another system
    # runs is not free: this system still infers on it, encodes it and serves
    # it. Deduplicating them let four cameras turn into seven running feeds.
    by_box = MAX_CAMERAS - (here + elsewhere)
    remaining = max(0, min(by_system, by_box))
    limit = ""
    if remaining <= 0:
        # Same order the writer refuses in: the system's own cap first -- it
        # is the one this page's operator can act on -- then the box's.
        limit = "system" if by_system <= 0 else "box"
    # Per-bus budget too, so the panel can grey out USB at two while IP still
    # has room -- the single remaining number above cannot say that.
    counts = _kind_counts(mine)
    by_kind = {k: {"used": counts.get(k, 0), "max": cap,
                   "remaining": max(0, cap - counts.get(k, 0))}
               for k, cap in KIND_LIMITS.items()}
    return {"max": MAX_CAMERAS, "max_per_system": MAX_CAMERAS_PER_SYSTEM,
            "max_configured": MAX_CONFIGURED_CAMERAS,
            "configured_here": len(mine),
            "used_here": here, "used_elsewhere": elsewhere,
            "remaining": remaining, "limit": limit,
            "by_kind": by_kind,
            # Whether the panel may offer the Depth (3D) kind at all.
            "depth_offered": depth_offered(config_dir)}


def _validate_camera(c, existing_ids=(), allow_unset=False):
    """One camera entry, cleaned. `allow_unset` is for RE-validating a stored
    list: a fresh system ships a placeholder row (source = "") until the
    operator picks hardware, and a whole-list rewrite (add a second camera,
    toggle one, remove one) must carry that row through rather than refuse
    the entire operation because of it -- that bug read as "cant add camera"
    on any system whose first camera was never set up. An unset row is kept
    DISABLED, so it never spends a budget or reaches the app. A NEW entry
    (add/set, allow_unset=False) still requires a real source."""
    source = str((c or {}).get("source") or "").strip()
    if source == "synthetic":
        if not allow_unset:
            raise ValueError(
                "'synthetic' is no longer a camera: AI vision needs a real "
                "one. Choose a USB, IP (RTSP), GigE or video-file source "
                "instead.")
        # A config written before synthetic was withdrawn reads as unset,
        # exactly as cameras() reports it.
        source = ""
    if not source and not allow_unset:
        raise ValueError("camera source is required (a device index like 0, "
                         "an RTSP/IP URL, 'gige', or a video file)")
    if SOURCE_REDACTED in source:
        # set_cameras/set_camera put a hidden password back before this; one
        # still here has nothing to come back from.
        raise ValueError("this camera's password is hidden from your account - "
                         "type the password again")
    if source.startswith("gige:") and not gige_name_ok(source.split(":", 1)[1]):
        raise ValueError("that GigE camera name has characters a camera name "
                         "cannot hold (quotes, backslashes, '!')")
    cid = _camera_id((c or {}).get("id"), "")
    if not cid:
        # Mint from the name, else a number -- an id is what zones and clip
        # filenames point at, so it must exist before anything references it.
        cid = _camera_id((c or {}).get("name"), "")
    if not cid:
        cid = f"cam{len(existing_ids) + 1}"
    out = {"id": cid, "source": source,
           "kind": "unset" if not source
           else camera_kind(source, (c or {}).get("kind")),
           "width": max(64, min(7680, int((c or {}).get("width") or 640))),
           "height": max(64, min(4320, int((c or {}).get("height") or 480))),
           "fps": max(MIN_CAMERA_FPS, min(60, int((c or {}).get("fps") or 10))),
           "enabled": bool((c or {}).get("enabled", True)) if source else False}
    # A GigE camera's exposure and gain. Kept only on a GigE entry: they are
    # aravissrc properties, and a USB camera carrying them would be settings
    # that quietly do nothing. Both default to continuous -- a camera that
    # adapts to the light is what a person expects a camera to do, and
    # aravissrc's own default ("off", meaning whatever the camera was last set
    # to by whatever touched it last) is the one behaviour nobody asked for.
    if out["kind"] == "gige":
        out["exposure_auto"] = _gige_auto((c or {}).get("exposure_auto"))
        out["gain_auto"] = _gige_auto((c or {}).get("gain_auto"))
        # The manual values are kept whatever the mode says, so switching to
        # auto and back does not lose the numbers that were dialled in. 1µs to
        # 10s, and 0-48dB: aravissrc's own range is wider, but a camera driven
        # past this is a mistake, and the camera clamps to its real limits.
        try:
            exposure = float((c or {}).get("exposure_us") or 0)
        except (TypeError, ValueError):
            exposure = 0.0
        if exposure > 0:
            out["exposure_us"] = round(max(1.0, min(10_000_000.0, exposure)), 1)
        try:
            gain = float((c or {}).get("gain_db") or 0)
        except (TypeError, ValueError):
            gain = 0.0
        if gain > 0:
            out["gain_db"] = round(max(0.0, min(48.0, gain)), 2)
        # Sensor binning: 1 (off), 2 or 4. Not a free-form number -- these are
        # the modes a sensor implements, and asking for 3 is asking for
        # nothing.
        try:
            bins = int((c or {}).get("binning") or 1)
        except (TypeError, ValueError):
            bins = 1
        if bins in (2, 4):
            out["binning"] = bins
    # What the operator adjusted on this camera's settings panel: brightness,
    # exposure, white balance -- whatever the DEVICE said it had. Carried as
    # the driver's own control names against plain numbers, because that is
    # what v4l2-ctl takes and inventing a vocabulary in between would mean a
    # translation table to keep in step with every camera ever plugged in.
    #
    # Kept on the entry rather than applied and forgotten: a control set live
    # is back to the factory default the moment the camera is replugged or the
    # app reopens it, which is how a night shift ends up with a dark picture
    # nobody changed.
    controls = (c or {}).get("controls")
    if isinstance(controls, dict) and controls:
        clean = {}
        # Bounded, because this SURVIVES: the table is merged on every save and
        # written into config.local.toml, a file the app re-reads and the hub
        # parses. Without a cap, repeated saves of distinct names grow it
        # without limit. A camera reports a dozen or so controls; taking the
        # first MAX_SAVED_CONTROLS in sorted order makes the truncation
        # deterministic rather than dependent on dict order.
        for key, value in sorted(controls.items())[:MAX_SAVED_CONTROLS]:
            key = str(key).strip()
            # A control name is a driver symbol. Anything else is either a
            # typo or an attempt to put something other than a name where a
            # name goes -- and this string reaches a command line.
            if not re.fullmatch(r"[a-z0-9_]{1,64}", key):
                continue
            try:
                # inf and nan arrive through JSON as floats (1e999 parses as
                # inf), and int(inf) is an OverflowError -- which used to be a
                # 500 on a request that is merely malformed.
                clean[key] = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
        if clean:
            out["controls"] = clean
    # What the measurement setup stored for this camera: the floor PLANE it
    # was calibrated against, and the per-object profiles saying which
    # numbers each class of object gets. Only a depth camera can carry one,
    # because only a depth camera can measure; on any other kind the table
    # would be settings that quietly do nothing.
    measure_cfg = (c or {}).get("measure")
    if out["kind"] == "depth" and isinstance(measure_cfg, dict):
        clean = _clean_measure(measure_cfg)
        if clean:
            out["measure"] = clean
    # What the workflow's Pre-process node put on this camera. Carried on ANY
    # kind of camera: it is software on the frame, which is the whole reason
    # the node exists -- an RTSP feed has no exposure dial to reach for.
    chain = _clean_preprocess((c or {}).get("preprocess"))
    if chain:
        out["preprocess"] = chain
        out["preprocess_preview"] = bool((c or {}).get("preprocess_preview"))
    # The snapshot trigger the workflow compiled onto this camera: kept for the
    # same reason as the chain above.
    snap = _clean_snapshot((c or {}).get("snapshot"))
    if snap:
        out["snapshot"] = snap
    name = str((c or {}).get("name") or "").strip()[:80]
    label = clean_label((c or {}).get("label"))
    if name:
        out["name"] = name
    if label:
        out["label"] = label
    return out


#: How many measurement profiles one camera may carry. A profile is one
#: object class and which of its numbers matter; a site has a handful of
#: things it measures, not hundreds.
MAX_MEASURE_PROFILES = 16

#: How many pre-process steps one camera may carry. The same number the app
#: stops at (systems/demo/app/preprocess.py MAX_STEPS) and the compiler
#: refuses past (workflow.MAX_PREPROCESS_STEPS): every step is work on every
#: frame, and a file that can grow without limit is a frame rate that can
#: fall without limit.
MAX_PREPROCESS_STEPS = 8

#: What a mask step may carry: the same zone budget the compiler enforces
#: (workflow.MAX_MASK_ZONES), and a point budget per shape. A zone drawn by
#: hand has a handful of corners; a thousand of them is a file that costs
#: more to parse than the mask costs to apply.
MAX_PREPROCESS_POLYGONS = 8
MAX_PREPROCESS_POINTS = 64


def _clean_preprocess(steps):
    """One camera's picture chain, sanitised for storage. [] when there is none.

    Shape only. WHICH ops exist and what their numbers may be is decided in
    two places already -- the compiler that writes the chain and the app that
    runs it (app/preprocess.py, which clamps every parameter and skips a step
    it cannot do) -- and a third opinion here would be a third thing to keep
    in step. What this guards is that the config file stays readable: a step
    is a table naming an `op` against flat values.

    The ORDER is the operator's and is never touched: blur then edges is not
    edges then blur, and the sequence is the whole meaning of the chain.
    """
    import math
    out = []
    if not isinstance(steps, list):
        return out
    for s in steps[:MAX_PREPROCESS_STEPS]:
        if not isinstance(s, dict):
            continue
        op = str(s.get("op") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", op):
            continue
        step = {"op": op}
        for key, value in s.items():
            key = str(key).strip()
            if key == "op" or not re.fullmatch(r"[a-z0-9_]{1,32}", key):
                continue
            if isinstance(value, bool) or isinstance(value, int):
                step[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                step[key] = value
            elif isinstance(value, str):
                step[key] = value[:32]
            elif isinstance(value, list):
                # The one nested value a step may carry: a mask's POLYGONS,
                # already resolved from zones to normalised 0..1 points by the
                # compiler. Kept as points and nothing else -- a step is a
                # config table, not a place to park arbitrary structure.
                shapes = []
                for shape in value[:MAX_PREPROCESS_POLYGONS]:
                    if not isinstance(shape, list):
                        continue
                    points = [[float(p[0]), float(p[1])]
                              for p in shape[:MAX_PREPROCESS_POINTS]
                              if isinstance(p, (list, tuple)) and len(p) == 2
                              and all(isinstance(v, (int, float))
                                      and not isinstance(v, bool)
                                      and math.isfinite(v) for v in p)]
                    if len(points) >= 3:
                        shapes.append(points)
                if shapes:
                    step[key] = shapes
        out.append(step)
    return out

#: The tools a profile or a manual measurement may name. Mirrors
#: app/measure.py: auto finds the object and its shape itself; the others
#: are the operator's taps.
MEASURE_TOOLS = ("auto", "sides", "circle", "line", "polygon", "outline")


def _clean_measure(cfg):
    """The camera's measurement table, sanitised. {} when nothing survives.

    The floor plane is four floats and the numbers that let a person judge
    it; a profile is a class name, a tool and which sides. Both are written by
    this box's own code, but they are re-read from a file anybody can edit,
    and a NaN in a plane normal is a measurement that is never wrong and
    never right.
    """
    import math
    out = {}
    floor = cfg.get("floor")
    if isinstance(floor, dict):
        try:
            normal = [float(v) for v in (floor.get("normal") or [])][:3]
            d = float(floor.get("d"))
            if (len(normal) == 3 and all(math.isfinite(v) for v in normal)
                    and math.isfinite(d) and any(abs(v) > 1e-6 for v in normal)):
                kept = {"normal": [round(v, 6) for v in normal], "d": round(d, 3)}
                for key in ("rms_mm", "centre_mm", "coverage"):
                    try:
                        value = float(floor.get(key))
                        if math.isfinite(value):
                            kept[key] = round(value, 3)
                    except (TypeError, ValueError):
                        pass
                at = str(floor.get("at") or "").strip()[:40]
                if at:
                    kept["at"] = at
                out["floor"] = kept
        except (TypeError, ValueError):
            pass
    # The remembered empty scene (background subtraction): a PNG under the
    # package's measure/ folder and the numbers a person checks. The file
    # name is pinned to that folder -- it is a path this box will open.
    background = cfg.get("background")
    if isinstance(background, dict):
        name = str(background.get("file") or "").replace("\\", "/")
        if re.fullmatch(r"measure/[A-Za-z0-9_.-]{1,80}\.png", name) and ".." not in name:
            kept = {"file": name}
            rgb = str(background.get("rgb_file") or "").replace("\\", "/")
            if re.fullmatch(r"measure/[A-Za-z0-9_.-]{1,80}\.png", rgb) and ".." not in rgb:
                kept["rgb_file"] = rgb
            for key in ("coverage",):
                try:
                    value = float(background.get(key))
                    if math.isfinite(value):
                        kept[key] = round(value, 3)
                except (TypeError, ValueError):
                    pass
            for key in ("width", "height"):
                try:
                    kept[key] = max(0, min(8192, int(background.get(key) or 0)))
                except (TypeError, ValueError):
                    pass
            at = str(background.get("at") or "").strip()[:40]
            if at:
                kept["at"] = at
            out["background"] = kept
    profiles = cfg.get("profiles")
    if isinstance(profiles, list):
        kept = []
        for row in profiles[:MAX_MEASURE_PROFILES]:
            if not isinstance(row, dict):
                continue
            tool = str(row.get("tool") or "auto").strip().lower()
            if tool not in MEASURE_TOOLS:
                continue
            entry = {"id": _camera_id(row.get("id"), "") or f"m{len(kept) + 1}",
                     "name": str(row.get("name") or "").strip()[:80],
                     # The class this profile measures. Empty = every object.
                     "cls": str(row.get("cls") or row.get("class") or "").strip()[:80],
                     "tool": tool,
                     "enabled": bool(row.get("enabled", True))}
            # A two-tap circle needs to know what its taps span.
            if tool == "circle":
                entry["mode"] = ("radius" if str(row.get("mode") or "").lower() == "radius"
                                 else "diameter")
            # Auto: measure the silhouette flat (2D) or the object's points (3D).
            if tool in ("auto", "outline") and str(row.get("dims") or "").lower() == "3d":
                entry["dims"] = "3d"
            # Which of the auto outline's sides to report, by index. Empty
            # means all of them.
            sides = row.get("sides")
            if isinstance(sides, list):
                entry["sides"] = sorted({int(v) for v in sides
                                         if isinstance(v, (int, float)) and 0 <= int(v) < 64})[:64]
            # Which of the object's numbers to publish. Empty means the
            # tool's defaults.
            fields = row.get("fields")
            if isinstance(fields, list):
                entry["fields"] = [str(f).strip()[:32] for f in fields
                                   if re.fullmatch(r"[a-z0-9_]{1,32}", str(f).strip())][:16]
            # Manual tools remember their taps as NORMALISED picture
            # coordinates (0..1), so they survive a resolution change.
            points = row.get("points")
            if isinstance(points, list) and tool != "auto":
                clean_pts = []
                for pt in points[:32]:
                    try:
                        u, v = float(pt[0]), float(pt[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                        clean_pts.append([round(u, 4), round(v, 4)])
                if clean_pts:
                    entry["points"] = clean_pts
                # The detection box the taps were placed on, normalised, so
                # they follow the object (see app/measure.py Measurer).
                anchor = row.get("anchor_box")
                if isinstance(anchor, (list, tuple)) and len(anchor) == 4:
                    try:
                        a = [round(float(v), 4) for v in anchor]
                        if (all(0.0 <= v <= 1.0 for v in a) and a[2] > a[0]
                                and a[3] > a[1]):
                            entry["anchor_box"] = a
                    except (TypeError, ValueError):
                        pass
            kept.append(entry)
        if kept:
            out["profiles"] = kept
    return out


@locked
def set_cameras(systems_dir, slug, entries, config_dir=None, allow_raw=True,
                also=None):
    """Write the whole [[cameras]] list into config.local.toml.

    Whole-list writes, like zones: config merging replaces a list wholesale
    rather than element-wise, so a partial write would silently drop the
    cameras it did not mention.

    A list read by an account that was shown masked passwords comes back
    with SOURCE_REDACTED in it; each is restored from the stored camera of
    the same id (unmask_source: exact round trip only). `allow_raw` False
    (the route's caller is not master/developer) refuses a NEW or CHANGED
    escape-hatch source (RawSourceRefused); unchanged ones pass.

    `also`, when given, is called with the local document just before it is
    written: a caller whose own change must land in the SAME write as the
    camera list (remove_camera dropping the camera's zones) does it there,
    so no reader ever sees the camera gone and its zones still present.

    The legacy [camera] table is kept in step with the FIRST enabled camera --
    an older engine build, the fleet adapter and anything else still reading the
    singular table keep working instead of losing the camera entirely.
    """
    if not isinstance(entries, list):
        raise ValueError("cameras must be a list")
    stored_sources = {str(c.get("id") or ""): str(c.get("source") or "")
                      for c in cameras(systems_dir, slug)}
    entries = [dict(e, source=unmask_source(e.get("source"),
                                            stored_sources.get(str(e.get("id") or ""))))
               if isinstance(e, dict) and SOURCE_REDACTED in str(e.get("source") or "")
               else e for e in entries]
    if len(entries) > MAX_CONFIGURED_CAMERAS:
        raise ValueError(f"at most {MAX_CONFIGURED_CAMERAS} camera entries "
                         "per system - delete the ones you no longer use")
    # The RUNNING limits: only enabled entries count, so any number of spares
    # may sit configured-but-off. Checked in the order the operator can act on
    # them: this system's own cap first, then the box's shared budget.
    running = sum(1 for e in entries
                  if isinstance(e, dict) and e.get("enabled", True))
    if running > MAX_CAMERAS_PER_SYSTEM:
        raise ValueError(
            f"one system runs at most {MAX_CAMERAS_PER_SYSTEM} cameras at "
            "once - switch another camera off first (its settings are kept)")
    # Then the per-BUS cap: a USB or GigE camera costs this box far more than
    # an IP one (see KIND_LIMITS), so the totals above are not the whole story.
    for kind, used in sorted(_kind_counts(entries).items()):
        cap = KIND_LIMITS.get(kind)
        if cap is not None and used > cap:
            what = KIND_LABELS.get(kind, kind)
            raise ValueError(
                f"one system runs at most {cap} {what} camera"
                f"{'' if cap == 1 else 's'} at once - this box captures those "
                "frames itself, and more than that is more than its CPU can "
                "keep up with. Switch another off first (its settings are kept)."
                if kind in ("usb", "gige") else
                f"one system runs at most {cap} {what} cameras at once - "
                "switch another off first (its settings are kept)")
    # The box counts running FEEDS. Re-using a camera another system already
    # runs is NOT free: this system runs its own inference on it, encodes its
    # own stream and serves its own preview. Counting devices instead let one
    # camera shared three ways cost a single slot, so a four-camera box ran
    # seven feeds.
    elsewhere = len(sources_elsewhere(systems_dir, slug, config_dir))
    if running + elsewhere > MAX_CAMERAS:
        raise ValueError(
            f"this box runs at most {MAX_CAMERAS} cameras at once, and "
            f"{elsewhere} of those are on other systems. A camera already "
            "used by another system counts again here - this one runs its "
            "own detection on it. Switch one off first (its settings are "
            "kept)."
            if elsewhere else
            f"this box runs at most {MAX_CAMERAS} cameras at once - switch "
            "one off to make room (its settings are kept)")
    # Depth sources this system ALREADY has: a rewrite (toggle, rename,
    # reorder) must carry them through; only a depth camera that was not
    # there before is refused where depth is not offered.
    had_depth = ({str(c.get("source") or "") for c in cameras(systems_dir, slug)
                  if c.get("kind") == "depth"}
                 if not depth_offered(config_dir) else set())
    clean, ids = [], set()
    for e in entries:
        # allow_unset: a stored placeholder row must survive a rewrite --
        # see _validate_camera. New entries were validated strictly by
        # their own route before reaching this whole-list write.
        c = _validate_camera(e, ids, allow_unset=True)
        _refuse_raw(c["source"], allow_raw, stored_sources.get(c["id"], ""))
        if (c["kind"] == "depth" and not depth_offered(config_dir)
                and c["source"] not in had_depth):
            raise ValueError(DEPTH_UNAVAILABLE)
        while c["id"] in ids:
            c["id"] = f"{c['id']}_2"
        ids.add(c["id"])
        clean.append(c)
    # An EMPTY list is allowed: removing every camera is how a system becomes
    # a blank canvas. It simply cannot START until a camera is added again --
    # start() refuses a system with no sourced camera.

    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    from core.services.passthrough_view import carry_view_sources   # noqa: PLC0415
    local["cameras"] = carry_view_sources(clean, _merged(pkg_dir))
    legacy = dict(local.get("camera") or {})
    if clean:
        primary = next((c for c in clean if c["enabled"]), clean[0])
        legacy.update({k: primary[k] for k in ("source", "width", "height", "fps")})
        if primary.get("label"):
            legacy["label"] = primary["label"]
        else:
            legacy.pop("label", None)
    else:
        # No cameras: blank the legacy table's source so an old reader stops
        # opening the removed device, and drop its stale label.
        legacy["source"] = ""
        legacy.pop("label", None)
    local["camera"] = legacy
    if also is not None:
        also(local)
    _write_local(pkg_dir, local)
    _forget_elsewhere()
    return clean


def set_camera(systems_dir, slug, source, width=None, height=None, fps=None,
               label=None, camera_id=None, kind=None, tuning=None, allow_raw=True):
    """Point ONE camera at a source. With no camera_id this edits the first
    camera, which is exactly the old single-camera behaviour.

    `label` is the human name of the picked device ("HD Pro Webcam C920"). The
    source spec cannot carry it -- a USB camera is the string "0" -- and
    re-deriving it later means re-enumerating devices, which produces a name
    that changes when the USB tree does. Stored once, at the moment the operator
    chose it, so what they saw is what stays on the screen."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("camera source is required (a device index like 0, "
                         "an RTSP/IP URL, 'gige', or a video file)")
    if source == "synthetic":
        raise ValueError(
            "'synthetic' is no longer a camera: AI vision needs a real one. "
            "Choose a USB, IP (RTSP), GigE or video-file source instead.")
    current = cameras(systems_dir, slug)
    cid = _camera_id(camera_id, "") if camera_id else ""
    target = next((c for c in current if c["id"] == cid), None) if cid else \
        (current[0] if current else None)
    if target is None:
        target = {"id": cid or (MAIN_CAMERA if not current else f"cam{len(current) + 1}")}
        current.append(target)
    # A source shown masked comes back masked: the stored password, on an
    # exact round trip only (unmask_source).
    source = unmask_source(source, target.get("source"))
    was_source = str(target.get("source") or "")
    was_label = str(target.get("label") or "")
    target["source"] = source
    # The kind travels with the source: repointing a camera from USB to RTSP
    # must not leave it counted against the USB budget. Dropping the stored
    # one makes _validate_camera re-derive it unless the caller declared it.
    target.pop("kind", None)
    if kind:
        target["kind"] = kind
    target["label"] = clean_label(label)
    if not target["label"]:
        target.pop("label", None)       # a new source with no name: no stale one
    # ...and the NAME, which is what every page actually displays. A camera
    # re-pointed at a different device kept the old device's name -- an IP
    # camera still called "LRCP U3-imx307" in the rail and in the dialog's
    # own header -- because only `label` was rewritten. The auto-derived name
    # follows the device; a name the OPERATOR chose (one that never matched
    # the device's own label) is theirs and is left alone.
    if source != was_source:
        name = str(target.get("name") or "")
        if not name or name == was_source or (was_label and name == was_label):
            if target.get("label"):
                target["name"] = target["label"]
            else:
                target.pop("name", None)
    if width:
        target["width"] = int(width)
    if height:
        target["height"] = int(height)
    if fps:
        target["fps"] = int(fps)
    # A GigE camera's exposure and gain. Written through even when they are
    # the mode's own defaults: "put it back on auto" is an edit like any
    # other, and a value only written when truthy could never be cleared.
    for key in ("exposure_auto", "exposure_us", "gain_auto", "gain_db",
                "binning"):
        if (tuning or {}).get(key) not in (None, ""):
            target[key] = tuning[key]
    saved = set_cameras(systems_dir, slug, current, allow_raw=allow_raw)
    return next((c for c in saved if c["id"] == target["id"]), saved[0])


def add_camera(systems_dir, slug, camera, config_dir=None, allow_raw=True):
    """Append a camera. The id is minted from the name when absent.

    With no RUNNING slot left the camera is still added -- switched off. The
    cap is on running cameras, not on how many an operator may set up, and
    "configure now, run tonight when the day cameras go off" is the normal
    reason to add a seventh.
    """
    current = cameras(systems_dir, slug)
    entry = _validate_camera(camera, {c["id"] for c in current})
    _refuse_raw(entry["source"], allow_raw)
    if entry["kind"] == "depth" and not depth_offered(config_dir):
        raise ValueError(DEPTH_UNAVAILABLE)
    if entry["enabled"]:
        budget = camera_budget(systems_dir, slug, config_dir)
        # Its own bus can be full while the box still has room (two USB
        # cameras on a box running four IP ones), so both are checked. Either
        # way the camera is still ADDED -- switched off, settings kept --
        # rather than refused, same as the box cap has always behaved.
        bus = (budget.get("by_kind") or {}).get(entry["kind"]) or {}
        if budget["remaining"] <= 0 or bus.get("remaining", 1) <= 0:
            entry["enabled"] = False
    return set_cameras(systems_dir, slug, current + [entry], config_dir,
                       allow_raw=allow_raw)


def enable_camera(systems_dir, slug, camera_id, enabled, config_dir=None):
    """Switch one camera on or off. Off keeps every setting -- and its zones
    and rules, which simply produce nothing until it runs again. Switching ON
    is what the running caps guard."""
    cid = _camera_id(camera_id, "")
    current = cameras(systems_dir, slug)
    target = next((c for c in current if c["id"] == cid), None)
    if target is None:
        raise ValueError(f"no camera {camera_id!r}")
    # A placeholder that was never pointed at hardware has nothing to switch
    # on -- and set_cameras would silently store it disabled anyway, which
    # reads as a toggle that does not work.
    if enabled and not str(target.get("source") or "").strip():
        raise ValueError("this camera has no source yet - choose a camera "
                         "for it on the Camera Setup page first")
    target["enabled"] = bool(enabled)
    return set_cameras(systems_dir, slug, current, config_dir)


def zones_on_camera(systems_dir, slug, camera_id):
    """The zones drawn on one camera: [{id, name, kind}], in saved order.

    "Drawn on" is _zone_camera's answer, the same one set_zones stores and
    the engine uses -- not a plain look at the `camera` field:
      * a zone naming this camera is on it;
      * a zone naming NO camera, or one this system no longer has, belongs to
        the DEFAULT camera (the first in the list), so it is on this camera
        only when this camera is that first one.
    Matching the field alone would leave every pre-multi-camera zone behind
    when the main camera went -- and those would then silently move onto
    whichever camera became first, drawn over a picture they were never
    meant for.
    """
    cid = _camera_id(camera_id, "")
    cams = cameras(systems_dir, slug)
    known = {c["id"] for c in cams}
    default_cam = cams[0]["id"] if cams else MAIN_CAMERA
    out = []
    for z in _merged(_pkg_dir(systems_dir, slug)).get("zones") or []:
        if not isinstance(z, dict):
            continue
        if _zone_camera(z, default_cam, known) != cid:
            continue
        zid = str(z.get("id") or z.get("name") or "zone")
        out.append({"id": zid, "name": str(z.get("name") or zid),
                    "kind": str(z.get("kind") or "area").lower()})
    return out


@locked
def remove_camera(systems_dir, slug, camera_id, with_zones=False):
    """Drop a camera -- the last one included -- and every zone drawn on it.

    A system with no camera is a blank canvas: it cannot start (start()
    refuses), and the page offers to add one. It used to refuse the last
    removal, which left every system permanently carrying at least one row it
    might not want.

    The camera's zones (zones_on_camera) go in the SAME write: a zone is a
    shape over one camera's picture, and kept without it, it either watched
    nothing or fell back onto the new first camera's picture, where its
    points mean nothing. Everything else that names those zones (the rules
    compiled from their actions, the Workflow canvas) is the caller's to
    tidy -- see the route. Returns the camera list, or (cameras, removed
    zones) with `with_zones`.
    """
    cid = _camera_id(camera_id, "")
    current = cameras(systems_dir, slug)
    kept = [c for c in current if c["id"] != cid]
    if len(kept) == len(current):
        raise ValueError(f"no camera {camera_id!r}")
    gone = zones_on_camera(systems_dir, slug, cid)
    drop = None
    if gone:
        gone_ids = {z["id"] for z in gone}
        # Written from the MERGED list, the way set_zones' callers send it:
        # a list replaces wholesale on merge, so zones that only lived in the
        # base config.toml must be copied across or they would come back.
        survivors = [z for z in _merged(_pkg_dir(systems_dir, slug)).get("zones") or []
                     if isinstance(z, dict)
                     and str(z.get("id") or z.get("name") or "zone") not in gone_ids]

        def drop(local):
            local["zones"] = survivors
    cams = set_cameras(systems_dir, slug, kept, also=drop)
    return (cams, gone) if with_zones else cams


#: A zone's default actions. The cap is generous but finite: every action
#: compiles into functions the app evaluates each frame.
MAX_ZONE_ACTIONS = 12

#: How a Cycle Time action measures. See _validate_zone for what each means.
CYCLE_MODES = ("countdown", "dwell", "timer", "stamps", "occupancy", "span")


def _zone_camera(z, default_camera=MAIN_CAMERA, known_cameras=None):
    """Which camera a zone was drawn on: its own, or the default when it names
    none -- or names one this system no longer has."""
    cid = _camera_id(z.get("camera"), "")
    if not cid:
        return default_camera
    if known_cameras and cid not in known_cameras:
        return default_camera
    return cid


#: A line's crossing threshold, percent of the picture width. Mirrors
#: systems/*/app/zones.py LINE_DEADBAND * 100 and LINE_BAND_PCT_MIN / MAX,
#: and must keep mirroring them: this decides what the hub writes, that
#: decides what the app counts, and tests/smoke_lineband.py pins the two
#: together. The default is never written (see _validate_zone).
LINE_BAND_DEFAULT_PCT = 1.2
LINE_BAND_MIN_PCT = 0.2
LINE_BAND_MAX_PCT = 5.0


def _validate_zone(z, default_camera=MAIN_CAMERA, known_cameras=None):
    """A cleaned zone dict, or ValueError. Points are NORMALIZED (0..1); the app
    stores nothing else, so the UI must send normalized coordinates."""
    kind = str(z.get("kind") or "area").lower()
    if kind not in ("area", "line"):
        raise ValueError(f"zone kind must be area or line, got {kind!r}")
    pts = [[float(x), float(y)] for x, y in (z.get("points") or [])]
    if kind == "line" and len(pts) != 2:
        raise ValueError("a line zone needs exactly 2 points")
    if kind == "area" and len(pts) < 3:
        raise ValueError("an area zone needs at least 3 points")
    for x, y in pts:
        if not (-0.001 <= x <= 1.001 and -0.001 <= y <= 1.001):
            raise ValueError("zone points must be normalized 0..1, not pixels")
    zid = str(z.get("id") or z.get("name") or "zone").strip() or "zone"
    clean = {
        "id": zid,
        "name": str(z.get("name") or zid).strip(),
        # Which camera's picture these normalised points were drawn over.
        # Absent = the system's first camera, which is where every zone drawn
        # before multi-camera came from. A zone naming a camera that no longer
        # exists falls back too, rather than becoming unreachable geometry.
        "camera": _zone_camera(z, default_camera, known_cameras),
        "kind": kind,
        "points": pts,
        "violation": bool(z.get("violation", False)),
        # A disabled zone keeps its geometry but is skipped by the engine and
        # the overlay -- so an operator can pause a zone without redrawing it.
        # Missing = enabled, which keeps every pre-existing config unchanged.
        "enabled": bool(z.get("enabled", True)),
    }
    # Containment mode: when does a detection count as "in" this zone.
    # "" (absent) = the app-wide anchor default, exactly as before.
    match = str(z.get("match") or "").strip().lower()
    if match in ("feet", "center", "full", "touch"):
        clean["match"] = match
    # Line face: does this line know an IN side, and which one. Only meaningful
    # on a line, and only stored when it is NOT the historic default, so every
    # config written before the field existed round-trips byte-identical.
    facing = str(z.get("facing") or "").strip().lower()
    if kind == "line" and facing in ("reverse", "none"):
        clean["facing"] = facing
    # Crossing threshold, percent of the picture width, lines only. Stored
    # only when it is not the factory band, so every config written before
    # the field existed round-trips byte-identical (the facing rule).
    # CLAMPED rather than dropped: a typed 15 means "wider", and turning it
    # into 1.2 would invert what was asked -- only the page's own range keeps
    # that from ever mattering. Blank, zero or negative = no override.
    if kind == "line":
        try:
            pct = float(z.get("band_pct"))
        except (TypeError, ValueError):
            pct = 0.0
        if pct > 0:
            pct = round(min(LINE_BAND_MAX_PCT, max(LINE_BAND_MIN_PCT, pct)), 1)
            if abs(pct - LINE_BAND_DEFAULT_PCT) > 1e-9:
                clean["band_pct"] = pct
    color = str(z.get("color") or "").strip()
    if len(color) == 7 and color[0] == "#":
        try:
            int(color[1:], 16)
            clean["color"] = color
        except ValueError:
            pass
    labels = [str(l).strip() for l in (z.get("labels") or []) if str(l).strip()]
    if labels:
        clean["labels"] = labels
    # Default actions: the no-code "zone -> classes -> actions" flow. Each is
    # compiled into [[functions]] (ids z_<zone>_*) by
    # functions.apply_zone_actions on every zone save.
    #
    # A zone may carry SEVERAL actions of the same type -- two violation rules
    # with different limits and classes is a real requirement, not an abuse --
    # so nothing here enforces one-per-type. Each action carries its own `id`,
    # which becomes part of the function ids it compiles to; an action without
    # one keeps the historic `z_<zone>_*` ids so existing dashboards and alert
    # history survive.
    actions, seen_action_ids = [], set()
    for a in (z.get("actions") or [])[:MAX_ZONE_ACTIONS]:
        if not isinstance(a, dict):
            continue
        atype = str(a.get("type") or "").strip()
        if atype not in ("count", "violation", "cycle"):
            continue
        act = {"type": atype}
        aid = "".join(c if c.isalnum() else "_"
                      for c in str(a.get("id") or "").strip().lower()).strip("_")[:20]
        if aid and aid not in seen_action_ids:
            seen_action_ids.add(aid)
            act["id"] = aid
        if atype == "count":
            # all       -- one number for the whole zone (or in/out for a line)
            # each      -- one number per class the zone filters on
            # selected  -- one number per class listed here
            mode = str(a.get("count_mode") or "all").strip().lower()
            act["count_mode"] = mode if mode in ("all", "each", "selected") else "all"
            if act["count_mode"] == "selected":
                act["classes"] = [str(c).strip() for c in (a.get("classes") or [])
                                  if str(c).strip()][:20]
        if atype == "violation":
            if a.get("limit") is not None:
                try:
                    act["limit"] = max(0, min(500, int(a["limit"])))
                except (TypeError, ValueError):
                    pass
            # How the count is compared with the limit. Stored per action so
            # "more than 2" and "fewer than 2" are both expressible; the engine
            # applies it in app/functions.py _update_unique.
            op = str(a.get("op") or ">").strip()
            act["op"] = op if op in PRESENCE_OPS else ">"
        if atype == "cycle":
            # countdown -- a time budget for the zone; arrivals spend it
            # dwell     -- measure only (per-object stays)
            # timer     -- alert once an object has been inside longer than max_s
            # stamps    -- log entry and exit times per object, as events
            # occupancy -- one clock while the zone is occupied at all
            # span      -- time from this zone/line to another one
            mode = str(a.get("mode") or "timer").strip().lower()
            act["mode"] = mode if mode in CYCLE_MODES else "timer"
            if act["mode"] == "span":
                act["start"] = str(a.get("start") or "").strip()[:40]
                act["end"] = str(a.get("end") or "").strip()[:40]
            if act["mode"] == "countdown":
                try:
                    act["start_s"] = max(1.0, min(604800.0,
                                                  float(a.get("start_s") or 60)))
                except (TypeError, ValueError):
                    act["start_s"] = 60.0
                try:
                    act["deduct_s"] = max(0.0, min(86400.0,
                                                   float(a.get("deduct_s", 1))))
                except (TypeError, ValueError):
                    act["deduct_s"] = 1.0
                if str(a.get("reset") or "").strip().lower() == "never":
                    act["reset"] = "never"
            if a.get("max_s") is not None:
                try:
                    act["max_s"] = max(1.0, min(86400.0, float(a["max_s"])))
                except (TypeError, ValueError):
                    pass
        alert = a.get("alert")
        if isinstance(alert, dict) and alert.get("on"):
            act["alert"] = {
                "on": True,
                "severity": "crit" if alert.get("severity") == "crit" else "warn",
                "message": str(alert.get("message") or "")[:120],
                # Capture is opt-in per action: nothing writes an image unless
                # asked here (or by a workflow Snapshot node). Still subject to
                # the System Control Snap / Record switches.
                "snapshot": bool(alert.get("snapshot")),
                "record": bool(alert.get("record")),
            }
        actions.append(act)
    if actions:
        clean["actions"] = actions
    # A depth range, millimetres from the camera, for a zone drawn over a
    # depth camera: with one, an object is in the zone only when it is also
    # this near. That is the third axis -- a flat polygon becomes a 3D zone.
    # Stored only when set, so every zone written before the field existed
    # round-trips byte-identical; the app treats 0 as "no bound".
    for key in ("depth_min_mm", "depth_max_mm"):
        try:
            mm = int(float(z.get(key) or 0))
        except (TypeError, ValueError):
            mm = 0
        if 0 < mm <= 20000:
            clean[key] = mm
    return clean


#: Output actions: fire a physical/logical signal when a matching event occurs.
#: Each is stored as an [[outputs]] table in config.local.toml; the detection app
#: fires enabled ones on each event (systems/<slug>/app/outputs.py).
#: How often a Snapshot / Record output may act while its trigger keeps
#: firing. Interpreted in systems/*/app/outputs.py:
#:   every    -- once per trigger event (the original behaviour)
#:   per_id   -- once per tracked object, so one person is captured once
#:   interval -- at most once every interval_s seconds
#:   once     -- once per episode; re-arms after interval_s quiet seconds
CAPTURE_MODES = ("every", "per_id", "interval", "once")

#: How a zone's Violation action compares how many objects are inside with the
#: limit the operator set. ">" is the common "too many"; "<" expresses the
#: opposite rule (a station that must never be left unattended).
PRESENCE_OPS = (">", ">=", "==", "<", "<=")

OUTPUT_TYPES = ("gpio", "io_out", "io_pwm", "mqtt", "mqtt_publish", "modbus",
                "modbus_reg", "modbus_write",
                "webhook", "webhook_publish", "email", "telegram", "snapshot",
                "record", "dataset", "zone_switch")

#: Which Modbus table a served register lives in. Holding is read/write and
#: what most PLCs poll by default; input is read-only, which is the honest
#: shape for a measurement -- nothing here is writable by the far end either
#: way, but a PLC engineer reading "input register" knows that without asking.
MODBUS_REG_KINDS = ("holding", "input")

#: Registers per block in the served datastore (app/modbus_server.py). An
#: address beyond it is refused HERE, at save time, rather than silently
#: never appearing on the wire.
MODBUS_MAX_ADDRESS = 511

#: The box's own "detection active" flag (INPUT register 511, 1 while the AI
#: runs): app/modbus_server.py STATUS_ADDRESS, mirrored. A register node may
#: not take it.
MODBUS_STATUS_ADDRESS = 511

#: What an io_out node does to its port when the rule fires. `on`/`off` latch
#: (a relay told to close stays closed); `pulse` is a blip; `toggle` flips.
IO_ACTIONS = ("on", "off", "pulse", "toggle")
OUTPUT_TRIGGERS = ("violation", "alert", "info", "any")

#: How many custom HTTP header rows a webhook output may carry. Eight is more
#: than any real REST endpoint asks for and keeps the request line bounded.
MAX_HEADERS = 8

#: Headers the ENGINE owns. urllib computes Host and Content-Length from the
#: request it actually sends; letting a stored document override either (or
#: Transfer-Encoding) is request smuggling waiting to happen, so they are
#: refused at the door rather than silently dropped later.
FORBIDDEN_HEADERS = ("host", "content-length", "transfer-encoding")

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _clean_http(o, clean, default_url=""):
    """Validate the HTTP fields shared by `webhook` and `webhook_publish`:
    url, method, headers and bearer token. Mutates and returns `clean`.

    Missing headers/token stay missing -- documents written before custom
    headers existed must behave exactly as they did."""
    url = str(o.get("url") or default_url).strip()[:500]
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("webhook url must start with http:// or https://")
    if url:
        refuse_own_address(url)
    clean["url"] = url
    method = str(o.get("method") or "POST").upper()
    clean["method"] = method if method in ("POST", "GET", "PUT") else "POST"

    rows = o.get("headers")
    if rows is not None:
        if not isinstance(rows, (list, tuple)):
            raise ValueError("headers must be a list of name/value rows")
        if len(rows) > MAX_HEADERS:
            raise ValueError(f"at most {MAX_HEADERS} headers")
        headers = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("each header is a {name, value} row")
            name = str(row.get("name") or "").strip()
            value = str(row.get("value") or "")
            if not name and not value:
                continue                    # blank row from the editor: drop it
            if not _HEADER_NAME_RE.match(name):
                raise ValueError(f"{name!r} is not a header name - letters, "
                                 "digits and '-' only, up to 64 characters")
            if name.lower() in FORBIDDEN_HEADERS:
                raise ValueError(f"the {name} header is set by the system and "
                                 "cannot be overridden")
            if len(value) > 500:
                raise ValueError(f"the {name} header value is too long "
                                 "(500 characters max)")
            headers.append({"name": name, "value": value})
        clean["headers"] = headers

    token = o.get("token")
    if token is not None:
        token = str(token).strip()
        if len(token) > 500:
            raise ValueError("the bearer token is too long (500 characters max)")
        clean["token"] = token
    return clean


#: The Advanced block on a message output: WHEN it may send. Flat scalars,
#: because these are written into a TOML config the detection app reads.
_MESSAGE_ADVANCED = ("cooldown_s", "hours_from", "hours_to", "days",
                     "from_date", "to_date")


def _keep_csv(o, clean):
    """Carry a message output's attached-CSV spec through validation.

    Written by the compiler from a CSV node's wires, read back by the route
    that renders the rows. The statement travels WITH the output so that what
    is sent is what was compiled -- the canvas can be edited afterwards
    without silently changing what last night's report will contain.

    Validated again here rather than trusted: this document is written by one
    route and read by another, and a boundary that trusts the previous
    boundary is not a boundary.
    """
    one = _clean_csv(o.get("csv_sql"), o.get("csv_tables"), o.get("csv_url"),
                     o.get("csv_name"), o.get("csv_node"), o.get("csv_rows"))
    if one is None:
        return
    clean["csv_sql"] = one["sql"]
    clean["csv_tables"] = one["tables"]
    clean["csv_url"] = one["url"]
    clean["csv_name"] = one["name"]
    # WHICH CSV node on the canvas wrote this. The spec lives on the MESSAGE
    # (that is what carries the file), so without this there is no way back
    # from the node an operator is looking at to the document that holds its
    # statement -- which is what the inspector's Download button asks for.
    if one["node"]:
        clean["csv_node"] = one["node"]
    clean["csv_rows"] = one["rows"]
    # Two or more CSV nodes into one message (Tok Aba, 2026-10-14): every
    # file, each checked exactly as the single one above. Written by the
    # compiler only when there are two or more, so a one-file message keeps
    # the shape it always had.
    if isinstance(o.get("csvs"), list):
        many = []
        for e in o["csvs"][:8]:
            e = e if isinstance(e, dict) else {}
            item = _clean_csv(e.get("sql"), e.get("tables"), e.get("url"),
                              e.get("name"), e.get("node"), e.get("rows"))
            if item is not None:
                many.append(item)
        if many:
            clean["csvs"] = many


def refuse_own_address(url):
    """A user-given URL may reach another computer, never this box (Pipi
    M-R1: a webhook to 127.0.0.1:9997 drove MediaMTX's control API, whose
    run hooks execute as its account). Judged here on the address as
    WRITTEN -- localhost, a loopback / link-local / unspecified / own IP in
    any spelling, this box's hostname -- without a DNS lookup, so reading a
    saved config never waits on the network; the app checks again at send
    time with the name resolved (core/services/netsafe.py). Raises
    ValueError with the sentence the editor shows."""
    from core.services import netsafe                         # noqa: PLC0415
    try:
        netsafe.check(url, resolve=lambda _h, _p: [], strict=False)
    except netsafe.Blocked as exc:
        raise ValueError(str(exc)) from exc


def note_master_port(systems_dir, slug, port):
    """Write this master's port into its package's config as [app]
    master_port, only when it changed, so an app the HUB starts (without the
    master's CYTRON_MASTER_PORT) still knows which port its CSV files and
    Telegram recipients may come from (Pipi N-3)."""
    port = int(port)
    pkg_dir = _pkg_dir(systems_dir, slug)
    if (_merged(pkg_dir).get("app") or {}).get("master_port") == port:
        return False
    with edit_local(pkg_dir) as local:
        local.setdefault("app", {})["master_port"] = port
    return True


def _master_route_problem(url, path):
    """"" when `url` is exactly this master's own `path` route, else why not
    (Pipi N-3: "any loopback URL" let a raw saved or imported output make the
    app fetch another local service, such as MediaMTX's path list). The port
    must be this master's own when this process knows it (a master does;
    the hub importing a design file does not, and the compile rebuilds the
    address anyway). The app checks the same again before it fetches."""
    from core.services import netsafe                         # noqa: PLC0415
    try:
        port = int(os.environ.get("CYTRON_MASTER_PORT") or 0) or None
    except ValueError:
        port = None
    return netsafe.master_url_problem(url, path, port)


def _keep_recipients(o, clean):
    """A Telegram node that sends to the people linked to its bot (Tok Aba,
    2026-10-14): `to_mode` "all" or "selected", the loopback address the app
    resolves them from at send time, and for "selected" the chosen Telegram
    ids -- numeric only (a group's with a leading -), at most 50. Nothing is
    kept for a node without a mode, so an old node stays exactly as it was."""
    mode = str(o.get("to_mode") or "").strip()
    if not mode or mode == "chat":
        return
    if mode not in ("all", "selected"):
        raise ValueError("unknown send-to choice for a Telegram node")
    url = str(o.get("to_url") or "").strip()
    why = _master_route_problem(url, "/api/telegram/recipients/resolve")
    if why:
        raise ValueError("a Telegram node's people are resolved by this box only "
                         f"(the address {why})")
    clean["to_mode"] = mode
    clean["to_url"] = url[:300]
    if mode == "selected":
        ids = [str(p).strip() for p in (o.get("to_people") or [])]
        if len(ids) > 50:
            raise ValueError("a Telegram node can send to at most 50 people")
        for pid in ids:
            if not re.fullmatch(r"-?\d{1,20}", pid):
                raise ValueError("a Telegram recipient is a numeric id")
        clean["to_people"] = list(dict.fromkeys(ids))


def _clean_csv(sql, tables, url, name, node, rows):
    """One attached CSV, re-checked: {sql, tables, url, name, node, rows}, or
    None when it has no statement. Raises on anything that must not run."""
    sql = str(sql or "").strip().rstrip(";").strip()
    if not sql:
        return None
    if ";" in sql or not re.match(r"^\s*(select|with)\b", sql, re.I):
        raise ValueError("an attached CSV may only SELECT - it reads the "
                         "database, it never changes it")
    kept = []
    for t in (tables if isinstance(tables, list) else [])[:12]:
        key = str((t or {}).get("key") or "").strip()
        if key:
            kept.append({"key": key[:60],
                         "name": str(t.get("name") or key)[:60],
                         "columns": [dict(c) for c in
                                     (t.get("columns") or [])[:60]]})
    if not kept:
        raise ValueError("an attached CSV needs the tables its Query may read")
    url = str(url or "").strip()
    # Localhost only, for the reason the query variable's url is: this address
    # is the app asking this box to run SQL for it, and a compiled document
    # naming another host would be the box fetching from somewhere nobody
    # chose.
    why = _master_route_problem(url, "/api/workflow/query-csv")
    if why:
        raise ValueError(f"an attached CSV is rendered by this box only (the address {why})")
    try:
        rows = max(0, min(100000, int(rows or 0)))
    except (TypeError, ValueError):
        rows = 0
    return {"sql": sql[:4000], "tables": kept, "url": url[:300],
            "name": str(name or "query.csv").strip()[:60] or "query.csv",
            "node": re.sub(r"[^A-Za-z0-9_-]", "", str(node or ""))[:40],
            "rows": rows}


def _keep_schedule(o, clean):
    """Carry a message output's send window through validation.

    NO LONGER CALLED for a compiled output: the window moved to the Schedule
    node, which is the node that already answers "when", and one clock in
    front of three messages beats the same window typed three times. Kept
    because a config written by hand -- or written before the move and not yet
    re-applied -- may still carry one, and the app still honours what it
    finds. Deleting this would silently un-mute those.

    Nothing here is a restriction unless it parses -- a half-typed time or a
    malformed date is dropped rather than stored, because the failure mode of
    a bad value is a SILENTLY MUTED alert, and that is discovered by the thing
    it was watching for happening unannounced.
    """
    for key in _MESSAGE_ADVANCED:
        raw = o.get(key)
        if raw in (None, ""):
            continue
        if key == "cooldown_s":
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            # A day is already "effectively never repeat"; beyond that the
            # number is a mistake, not an intention.
            clean[key] = round(min(value, 86400.0), 2)
        elif key in ("hours_from", "hours_to"):
            text = str(raw).strip()
            if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", text):
                clean[key] = text
        elif key in ("from_date", "to_date"):
            text = str(raw).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                clean[key] = text
        elif key == "days":
            names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            want = [d.strip().lower()[:3] for d in str(raw).split(",")]
            want = [d for d in want if d in names]
            # EVERY day selected is the same as no restriction, and storing it
            # as one means a later change to the day names could mute the node.
            if want and len(set(want)) < 7:
                clean[key] = ",".join(sorted(set(want), key=names.index))


def _keep_vars(o, clean):
    """Carry a message output's {placeholder: stat_key} map through validation.

    Built by the compiler from the wires into an Email/Telegram node, never
    typed, so the only job here is to make sure what arrives is a flat map of
    plain names to plain keys -- this is written into a config file the
    detection app reads, and a nested structure would break the TOML writer
    long after anyone remembers why.
    """
    raw = o.get("vars")
    if not isinstance(raw, dict) or not raw:
        return
    out = {}
    for name, key in list(raw.items())[:40]:
        name = str(name or "").strip()[:60]
        key = str(key or "").strip()[:120]
        # A placeholder is substituted by literal text replacement, so a name
        # carrying a brace would produce a token that can never match, and one
        # carrying nothing would replace every "{}" in the message.
        if not name or not key or "{" in name or "}" in name:
            continue
        out[name] = key
    if out:
        clean["vars"] = out


#: The four Modbus WRITE function codes, with the most values each may carry
#: in one request (the protocol's own limits, Modbus spec 6.11/6.12). 5 and 6
#: write exactly one.
MODBUS_WRITE_FCS = {5: 1, 6: 1, 15: 1968, 16: 123}
#: How a Modbus Write decides to write: on its wired trigger, or whenever the
#: wired value changes (no trigger wired).
#: trigger -- write when the wired trigger fires; change -- write when the
#: value changes; fire -- the value port carries Injects/Schedules only and
#: every one of their fires writes, newest wire first (`sources` names each
#: wire's fired key and value key).
MODBUS_WRITE_MODES = ("trigger", "change", "fire")


def clean_modbus_write_frame(frame):
    """The custom request layout of a Modbus Write (its Advanced editor), or
    None for the standard frame. Unlike a Read's frame it cannot be built at
    save time -- the VALUE arrives at run time -- so the app builds the bytes
    on every write from this layout:

        {"fc": 0-255, "fields": [{"key": st|q|bc|val|custom, "size", "order",
                                  "hex" (custom only)}]}

    st = the node's address, q = its quantity, bc = the byte count of the data,
    val = the data itself (the value written), custom = fixed hex.
    """
    if not isinstance(frame, dict) or not frame.get("custom"):
        return None
    try:
        fc = int(str(frame.get("fc") or "06"), 16)
    except (TypeError, ValueError):
        raise ValueError("custom frame: FC must be hex 00-FF")
    if not 0 <= fc <= 255:
        raise ValueError("custom frame: FC must be hex 00-FF")
    fields = []
    for f in (frame.get("fields") or [])[:10]:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "")
        if key not in ("st", "q", "bc", "val", "custom"):
            raise ValueError(f"custom frame: unknown field {key!r}")
        try:
            size = int(f.get("size") or 2)
        except (TypeError, ValueError):
            size = 2
        if size not in (1, 2, 3, 4, 6, 8):
            raise ValueError("custom frame: a field is 1, 2, 3, 4, 6 or 8 bytes")
        row = {"key": key, "size": size,
               "order": "lo" if f.get("order") == "lo" else "hi",
               "name": str(f.get("name") or key).strip()[:16]}
        if key == "custom":
            raw = re.sub(r"\s+", "", str(f.get("hex") or ""))
            if not re.fullmatch(r"[0-9A-Fa-f]{1,16}", raw):
                raise ValueError(f"custom frame: field {row['name']} is not hex")
            row["hex"] = raw.upper()
        fields.append(row)
    if not any(f["key"] == "val" for f in fields):
        raise ValueError("custom frame: a write needs its Value field")
    # Same shape it came in (custom, FC as hex), so cleaning it twice -- the
    # compile, then the output document's own check -- changes nothing.
    return {"custom": True, "fc": f"{fc:02X}", "fields": fields}


def _clean_modbus_write(o, clean):
    """The Modbus Write node's document. Mutates and returns `clean`.

    Refused rather than clamped, unlike the connection's timeouts: an address
    or a function code the operator meant and did not get writes a DIFFERENT
    register on somebody's machine, which is not a default anybody wants.
    """
    server = str(o.get("server") or "").strip()[:40]
    if not server:
        raise ValueError("Modbus Write needs a Server - pick one, or add one "
                         "with +")
    clean["server"] = server
    try:
        fc = int(o.get("fc") or 0)
    except (TypeError, ValueError):
        fc = 0
    if fc not in MODBUS_WRITE_FCS:
        raise ValueError("FC must be 5, 6, 15 or 16")
    clean["fc"] = fc
    raw = o.get("address")
    if raw in (None, ""):
        raise ValueError("Modbus Write needs an Address (0-65535)")
    try:
        address = int(raw)
    except (TypeError, ValueError):
        raise ValueError("the Address must be a whole number")
    if not 0 <= address <= 65535:
        raise ValueError("the Address must be between 0 and 65535")
    clean["address"] = address
    raw = o.get("quantity")
    if raw in (None, ""):
        raise ValueError("Modbus Write needs a Quantity (1-65535)")
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        raise ValueError("the Quantity must be a whole number")
    if not 1 <= quantity <= 65535:
        raise ValueError("the Quantity must be between 1 and 65535")
    # The field takes Node-RED's 1-65535, but one request cannot: FC 15 moves
    # at most 1968 coils and FC 16 at most 123 registers. Said at save time,
    # because the device's answer to more is an exception code nobody reads.
    if fc in (15, 16) and quantity > MODBUS_WRITE_FCS[fc]:
        raise ValueError(f"FC {fc} writes at most {MODBUS_WRITE_FCS[fc]} "
                         f"{'coils' if fc == 15 else 'registers'} at once")
    clean["quantity"] = quantity if fc in (15, 16) else 1
    unit = o.get("unit")
    if unit not in (None, ""):
        try:
            unit = int(unit)
        except (TypeError, ValueError):
            raise ValueError("the Unit-Id must be a whole number")
        if not 0 <= unit <= 255:
            raise ValueError("the Unit-Id must be between 0 and 255")
        clean["unit"] = unit
    mode = str(o.get("mode") or "change").lower()
    clean["mode"] = mode if mode in MODBUS_WRITE_MODES else "change"
    clean["key"] = str(o.get("key") or "").strip()[:60]
    if not clean["key"]:
        raise ValueError("Modbus Write needs a value wired into it")
    # The rule whose every fire writes again in change mode, as an MQTT
    # Publish's `beat` (app/outputs.py _modbus_write_tick).
    beat = str(o.get("beat") or "").strip()[:60]
    if beat and clean["mode"] == "change":
        clean["beat"] = beat
    if clean["mode"] == "fire":
        sources = [{"fired": str(s.get("fired") or "").strip()[:60],
                    "key": str(s.get("key") or "").strip()[:60]}
                   for s in (o.get("sources") or [])[:8] if isinstance(s, dict)]
        sources = [s for s in sources if s["fired"] and s["key"]]
        if not sources:
            raise ValueError("a Modbus Write that writes on each inject needs "
                             "the Injects' fired keys")
        clean["sources"] = sources
    payload_fn = str(o.get("payload_fn") or "").strip()[:40]
    if payload_fn:
        clean["payload_fn"] = payload_fn
    frame = clean_modbus_write_frame(o.get("frame"))
    if frame:
        clean["frame"] = frame
    clean["delay_start"] = bool(o.get("delay_start"))
    if clean["delay_start"]:
        try:
            wait = float(o.get("start_delay_s") if o.get("start_delay_s")
                         not in (None, "") else 5)
        except (TypeError, ValueError):
            raise ValueError("the start delay must be a number of seconds")
        clean["start_delay_s"] = round(max(0.0, min(600.0, wait)), 2)
    else:
        clean["start_delay_s"] = 0.0
    return clean


#: An MQTT Connection id (core/services/mqtt_clients.py mints them).
_MQTT_CONN_RE = re.compile(r"^mq_[0-9a-f]{8}$")


def _mqtt_where(o, clean):
    """Where an MQTT output publishes, into `clean`.

    A named MQTT Connection -- `conn`, the id the app looks up in
    [[mqtt_clients]] -- for anything compiled since Connections exist; the
    login stays in that table and never in the output document, which the
    outputs route hands to anyone who can read a workflow. A document saved
    before Connections keeps its own host and port and publishes as it did,
    and one with no Connection picked publishes to this box's broker.
    """
    cid = str(o.get("conn") or o.get("connection") or "").strip()
    if cid:
        if not _MQTT_CONN_RE.match(cid):
            raise ValueError("that MQTT Connection does not exist - pick another")
        clean["conn"] = cid
        return
    # None picked (a new node, or one nobody has set up): THIS box's broker,
    # which is what every MQTT node defaulted to before Connections -- a
    # workflow must not stop compiling because a field it never needed is
    # now offered. The app logs in with the box's own broker login.
    clean["host"] = str(o.get("host") or "127.0.0.1")[:120]
    clean["port"] = int(o.get("port") or 1883)


def _validate_output(o):
    """A cleaned output-action dict, or ValueError. Type-specific params are kept
    only for the chosen type, so a saved action never carries stale fields."""
    otype = str(o.get("type") or "").lower()
    if otype not in OUTPUT_TYPES:
        raise ValueError(f"output type must be one of {', '.join(OUTPUT_TYPES)}")
    trigger = str(o.get("trigger") or "violation").lower()
    if trigger not in OUTPUT_TRIGGERS:
        raise ValueError(f"trigger must be one of {', '.join(OUTPUT_TRIGGERS)}")
    clean = {
        "id": str(o.get("id") or f"out_{otype}")[:40],
        "label": str(o.get("label") or otype.upper())[:60],
        "type": otype,
        "trigger": trigger,
        "enabled": bool(o.get("enabled", False)),
    }
    # Optional per-function binding (workflow Analytics -> Output wire): fire
    # only on events raised by this analytics function. Empty = old behaviour,
    # any event of the matching type.
    fn = str(o.get("function") or "").strip()[:40]
    if fn:
        clean["function"] = fn
    # Hold the send for this long after the trigger (workflow Delay node).
    # Capped at an hour: a wait measured in days is a scheduler, not an
    # output, and the timer holding it would not survive that long anyway.
    if o.get("delay_s") not in (None, ""):
        try:
            delay = float(o["delay_s"])
        except (TypeError, ValueError):
            raise ValueError("delay must be a number of seconds")
        if not 0 <= delay <= 3600:
            raise ValueError("delay must be between 0 and 3600 seconds")
        if delay > 0:
            clean["delay_s"] = round(delay, 2)
    if otype == "gpio":
        clean["pin"] = int(o.get("pin") or 0)
        clean["pulse_ms"] = int(o.get("pulse_ms") or 0)   # 0 = latch on, else pulse
        clean["active_high"] = bool(o.get("active_high", True))
    elif otype == "mqtt":
        _mqtt_where(o, clean)
        clean["topic"] = str(o.get("topic") or "cytron/alert")[:200]
        clean["payload"] = str(o.get("payload") or "1")[:200]
    elif otype == "modbus_reg":
        # A register we SERVE. The value arrives from the wired rule
        # (stats[key]) and is written every tick, because a PLC scans on its
        # own clock and must never read whatever the last alert left behind.
        clean["key"] = str(o.get("key") or "").strip()[:60]
        if not clean["key"]:
            raise ValueError("a Modbus Register needs a value wired into it")
        try:
            address = int(o.get("address") or 0)
        except (TypeError, ValueError):
            raise ValueError("the Modbus address must be a whole number")
        if not 0 <= address <= MODBUS_MAX_ADDRESS:
            # Refused here rather than clamped: an address the operator meant
            # and did not get is a register a PLC reads as 0 for ever, with
            # nothing anywhere saying why.
            raise ValueError(
                f"the Modbus address must be between 0 and {MODBUS_MAX_ADDRESS}")
        clean["address"] = address
        kind = str(o.get("reg_kind") or "holding").lower()
        if kind not in MODBUS_REG_KINDS:
            raise ValueError("register must be a holding or input register")
        if kind == "input" and address == MODBUS_STATUS_ADDRESS:
            raise ValueError(
                f"input register {MODBUS_STATUS_ADDRESS} is the box's own "
                "'detection active' flag - choose another address")
        clean["reg_kind"] = kind
        # A register is a 16-bit INTEGER, so 2.5 would arrive as 2. The scale
        # is how a decimal survives the wire: at 10, 2.5 is sent as 25 and the
        # far end divides. Bounded so a typo cannot turn every value into its
        # clamped maximum.
        # `or 1` would be wrong here: a scale of 0 is falsy, so a typed zero
        # would silently become 1 and every value on that register would be
        # off by whatever the operator meant to divide by. Missing is 1;
        # zero is an error.
        raw_scale = o.get("scale")
        try:
            scale = 1.0 if raw_scale in (None, "") else float(raw_scale)
        except (TypeError, ValueError):
            raise ValueError("the Modbus scale must be a number")
        if not 0 < scale <= 10000:
            raise ValueError("the Modbus scale must be between 0 and 10000")
        clean["scale"] = scale
    elif otype == "zone_switch":
        # Flips a ZONE at runtime: enabled while the wired value (stats[key])
        # is truthy, disabled while falsy; invert swaps the two. The zone's
        # SAVED enabled flag is never written -- a restart restores it.
        clean["zone"] = str(o.get("zone") or "").strip()[:60]
        if not clean["zone"]:
            raise ValueError("a Zone Switch needs a zone to switch")
        clean["key"] = str(o.get("key") or "").strip()[:60]
        if not clean["key"]:
            raise ValueError("a Zone Switch needs a value wired into it")
        # What a 1 on the wire DOES, said plainly, rather than a polarity
        # flag the operator has to reason backwards through: "enable" arms
        # the zone while the value is 1, "disable" silences it while the
        # value is 1. (A graph saved with the old invert flag still loads:
        # invert = "1 means off" = disable.)
        action = str(o.get("action") or "").strip().lower()
        if action not in ("enable", "disable"):
            action = "disable" if o.get("invert") else "enable"
        clean["action"] = action
    elif otype == "mqtt_publish":
        # Telemetry rather than an alert: publish a value on a schedule. It
        # carries a `key` (which stat to send) instead of a fixed payload, and
        # an interval instead of a trigger.
        _mqtt_where(o, clean)
        clean["topic"] = str(o.get("topic") or "cytron/value")[:200]
        clean["key"] = str(o.get("key") or "").strip()[:60]
        # No key: nothing wired in. The node publishes its own payload (the
        # time when blank) on its interval, so a broker and a topic are
        # enough (client UAT 2026-10-04, item 1). Only a document that SAYS
        # so -- a payload field, even blank -- may be keyless; a hand-written
        # one with neither is still a mistake.
        if not clean["key"]:
            if o.get("payload") is None:
                raise ValueError("an MQTT publisher needs a value to publish")
            clean["payload"] = str(o.get("payload") or "")[:200]
        fmt = str(o.get("format") or "value").lower()
        clean["format"] = fmt if fmt in ("value", "json") else "value"
        # WHEN, from what is wired in (services/publish_follow.py): an Inject's
        # fire, a change, a Trigger's fire. No mode: compiled before, own clock.
        mode = str(o.get("mode") or "")
        if mode in ("change", "fire") and clean["key"]:
            clean["mode"] = mode
            clean.update({k: str(o[k]).strip()[:60] for k in ("fired", "beat")
                          if str(o.get(k) or "").strip()})
            if mode == "fire" and not clean.get("fired"):
                raise ValueError("an MQTT Publish on fire needs the fire it follows")
        else:
            try:
                gap = float(o.get("interval_s") or 5)
            except (TypeError, ValueError):
                gap = 5.0
            # Faster than 5/s floods the broker; slower than an hour is a schedule.
            clean["interval_s"] = min(3600.0, max(0.2, gap))
        clean["retain"] = bool(o.get("retain", False))
        clean["send_missing"] = bool(o.get("send_missing", False))
    elif otype in ("io_out", "io_pwm"):
        # Addressed by the SILKSCREEN name (DO0, L1), never by pin: the pin is
        # the board's business, and a workflow that named one would quietly
        # mean something else on the next revision.
        from core.services import board_io                  # noqa: PLC0415

        port = str(o.get("port") or "").upper()
        allowed = board_io.PWM_PORTS if otype == "io_pwm" else board_io.OUTPUT_PORTS
        if port not in allowed:
            raise ValueError(f"port must be one of {', '.join(allowed)}")
        clean["port"] = port
        if otype == "io_out":
            action = str(o.get("action") or "on").lower()
            if action not in IO_ACTIONS:
                raise ValueError(f"action must be one of {', '.join(IO_ACTIONS)}")
            clean["action"] = action
            # A pulse is a blip to a machine, not a schedule: past a minute it
            # is a latch that someone forgot to turn off.
            clean["pulse_ms"] = max(0, min(60000, int(o.get("pulse_ms") or 0)))
            clean["active_high"] = bool(o.get("active_high", True))
        else:
            source = str(o.get("source") or "fixed").lower()
            clean["source"] = source if source in ("fixed", "value") else "fixed"
            # WHEN the level is written: on the rule's alert, or continuously.
            # A brightness that follows a value has to be "always" -- a level
            # updated only when something goes wrong is not a level.
            when = str(o.get("when") or "alert").lower()
            clean["when"] = when if when in ("alert", "always") else "alert"
            # `level` is 0-255 (the byte convention every PWM datasheet
            # uses); `percent` is the older field, still accepted so a saved
            # config keeps meaning what it did.
            try:
                if o.get("level") not in (None, ""):
                    level = float(o["level"])
                    if not 0 <= level <= 255:
                        raise ValueError
                    percent = level / 255.0 * 100.0
                else:
                    percent = float(o.get("percent") or 0)
                    if not 0 <= percent <= 100:
                        raise ValueError
            except (TypeError, ValueError):
                raise ValueError("level must be a number between 0 and 255")
            clean["percent"] = round(max(0.0, min(100.0, percent)), 1)
            if clean["source"] == "value":
                clean["key"] = str(o.get("key") or "").strip()[:60]
                if not clean["key"]:
                    raise ValueError("a PWM output driven by a value needs a "
                                     "value wired into it")
                try:
                    clean["in_min"] = float(o.get("in_min") or 0)
                    clean["in_max"] = float(o.get("in_max") or 100)
                except (TypeError, ValueError):
                    raise ValueError("the input range must be two numbers")
                if clean["in_min"] == clean["in_max"]:
                    raise ValueError("the input range needs two different "
                                     "numbers - otherwise every value maps "
                                     "to the same brightness")
            # Software PWM: fine for a lamp, wrong for a servo, so the
            # frequency is settable and bounded to what a GPIO can hold.
            clean["frequency"] = max(50, min(20000, int(o.get("frequency") or 1000)))
    elif otype == "modbus_write":
        _clean_modbus_write(o, clean)
    elif otype == "modbus":
        clean["device"] = str(o.get("device") or "/dev/ttyAMA2")[:120]
        clean["unit"] = int(o.get("unit") or 1)
        clean["register"] = int(o.get("register") or 0)
        clean["value"] = int(o.get("value") or 1)
        # RS485 transport, carried rather than assumed: 9600 is common and
        # wrong on plenty of installations, and a coil is as ordinary a
        # destination as a holding register. Either mismatch is a silent
        # no-op on the wire.
        clean["baudrate"] = int(o.get("baudrate") or 9600)
        target = str(o.get("target") or "holding").lower()
        clean["target"] = target if target in ("holding", "coil") else "holding"
    elif otype == "webhook":
        _clean_http(o, clean)
        clean["payload"] = str(o.get("payload") or "")[:2000]
    elif otype == "webhook_publish":
        # The HTTP twin of mqtt_publish: telemetry rather than an alert. It
        # carries a `key` (which stat to send) instead of a fixed payload, and
        # an interval instead of a trigger.
        _clean_http(o, clean)
        clean["key"] = str(o.get("key") or "").strip()[:60]
        if not clean["key"]:
            raise ValueError("an HTTP publisher needs a value to publish")
        fmt = str(o.get("format") or "value").lower()
        clean["format"] = fmt if fmt in ("value", "json") else "value"
        try:
            gap = float(o.get("interval_s") or 5)
        except (TypeError, ValueError):
            gap = 5.0
        # Same bounds as mqtt_publish: faster than 5/s hammers the endpoint,
        # slower than an hour is a schedule, not telemetry.
        clean["interval_s"] = min(3600.0, max(0.2, gap))
        clean["send_missing"] = bool(o.get("send_missing", False))
    elif otype == "email":
        # Recipients only -- the SMTP settings come from the system's email
        # config at compile time, so an operator wiring an alert never has to
        # know the mail server, and changing servers is one edit, not N.
        to = o.get("to")
        if isinstance(to, str):
            to = [p for p in (x.strip() for x in to.replace(";", ",").split(",")) if p]
        to = [str(t).strip()[:120] for t in (to or []) if str(t).strip()][:10]
        for addr in to:
            if "@" not in addr or addr.startswith("@") or addr.endswith("@"):
                raise ValueError(f"{addr!r} is not an email address")
        clean["to"] = to
        clean["subject"] = str(o.get("subject") or "")[:200]
        clean["body"] = str(o.get("body") or "")[:1000]
        # WHICH mailbox sends, as an id from Settings > Email. Carried
        # through validation because the compiler resolves it into real SMTP
        # settings afterwards (workflow._with_mail_settings); blank means the
        # box's default. Never credentials -- the node names an account, it
        # does not carry one.
        account = str(o.get("account") or "").strip()[:60]
        if account:
            clean["account"] = account
        # Filled in by the compiler from the resolved mailbox.
        for key in ("smtp_host", "smtp_user", "smtp_pass", "from_addr"):
            if o.get(key):
                clean[key] = str(o[key])[:200]
        if o.get("smtp_port"):
            clean["smtp_port"] = int(o["smtp_port"])
        _keep_vars(o, clean)
        _keep_csv(o, clean)
    elif otype == "telegram":
        token = str(o.get("bot_token") or "").strip()[:120]
        if token and not re.match(r"^\d+:[\w-]{20,}$", token):
            raise ValueError("bot_token looks wrong - it is the '123456:ABC-…' "
                             "string BotFather gives you")
        clean["bot_token"] = token
        clean["chat_id"] = str(o.get("chat_id") or "").strip()[:60]
        clean["message"] = str(o.get("message") or "")[:1000]
        # Which of the box's bots this posts as (Settings > Telegram). The
        # compiler swaps it for the real token on the way to disk, the same
        # way an Email node's mailbox becomes SMTP settings -- so a graph
        # carries the NAME of a credential and never the credential.
        account = str(o.get("account") or "").strip()[:40]
        if account:
            clean["account"] = account
        _keep_recipients(o, clean)
        _keep_vars(o, clean)
        _keep_csv(o, clean)
    elif otype == "dataset":
        # A row of the operator's own columns, written into the database.
        # The column NAMES become SQL fragments when the dataset is queried
        # later (cols->>'name' -- identifiers cannot be bound), so the same
        # narrow pattern the repo enforces is enforced here, at the only door
        # through which a stored document can arrive.
        from core.repos import datasets as _ds                # noqa: PLC0415
        key = str(o.get("dataset") or "").strip()
        if not _ds.COL_RE.match(key):
            raise ValueError("a dataset name is letters, numbers and "
                             "underscores, starting with a letter")
        clean["dataset"] = key
        clean["name"] = str(o.get("name") or key)[:60]
        # A row written when the stay behind its Trigger ends (the Trigger's
        # "wait for the timer" setting; Workflow.compile sets it).
        if o.get("wait_end"):
            clean["wait_end"] = True
        mode = str(o.get("mode") or "alert").lower()
        clean["mode"] = (mode if mode in ("alert", "every", "change", "crossing")
                         else "alert")
        # "When the same object is seen again": kept only when it asks for
        # something, so a table on the default (add a new row) stays
        # byte-identical to the document it was before the setting existed.
        if str(o.get("on_repeat") or "").lower() == "update":
            clean["on_repeat"] = "update"
        rows = o.get("columns")
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError("a dataset needs at least one column")
        if len(rows) > 16:
            raise ValueError("a dataset holds at most 16 columns")
        cols, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("each column is a {name, type, src} row")
            name = str(row.get("name") or "").strip()
            if not _ds.COL_RE.match(name):
                raise ValueError(f"{name!r} is not a column name - letters, "
                                 "numbers and underscores, starting with a letter")
            if name in seen:
                raise ValueError(f"two columns are both called {name!r}")
            seen.add(name)
            kind = str(row.get("type") or "number")
            src = str(row.get("src") or "wire")
            col = {"name": name,
                   "type": kind if kind in _ds.COL_TYPES else "number",
                   "src": src if src in ("wire", "stat", "event", "const") else "wire"}
            # `zone` scopes a track-id column to a zone's members (a zone
            # counter's PID port), so "in frame" ids stay out of it.
            for field in ("key", "field", "handle", "zone"):
                if row.get(field):
                    col[field] = str(row[field])[:60]
            if col["src"] == "const":
                col["value"] = str(row.get("value") if row.get("value") is not None
                                   else "")[:120]
            cols.append(col)
        clean["columns"] = cols
        # "Object IDs in a row": one row for the whole group. Kept only when it
        # can mean something -- an identity column to list the ids in, and a
        # mode that is not one-row-per-crossing -- so the default and every
        # document saved before it stay byte-identical.
        if (str(o.get("ids_in_row") or "").lower() == "group"
                and clean["mode"] != "crossing"
                and any(c["src"] == "event" and c.get("field") == "track_id"
                        for c in cols)):
            clean["ids_in_row"] = "group"
        if clean["mode"] == "crossing":
            # One row per crossing of ONE line. The line id is what
            # app/outputs.py fire() matches against a crossing event's
            # detail.zone -- crossing events carry no detail.function, so the
            # function binding above cannot do this job. `classes` mirrors the
            # counter's Class Filter so a car over a people line writes no row.
            line = str(o.get("line") or "").strip()[:60]
            if not line:
                raise ValueError("a table written on each line crossing needs "
                                 "the line it watches")
            clean["line"] = line
            classes = [str(c).strip()[:60] for c in (o.get("classes") or [])
                       if str(c).strip()][:32]
            if classes:
                clean["classes"] = classes
        # The clock fields belong to the two clock-paced modes only: an alert
        # document stays byte-identical to what it was, and a crossing
        # document carries no interval it would never honour.
        if clean["mode"] in ("every", "change"):
            try:
                gap = float(o.get("interval_s") or 60)
            except (TypeError, ValueError):
                gap = 60.0
            # Floor of 5s on purpose: these rows go through events.jsonl, and a
            # once-a-second dataset writes ~86k events a day into a log that
            # every restart re-reads from the top.
            clean["interval_s"] = min(3600.0, max(5.0, gap))
            got = o.get("min_interval_s")
            try:
                floor = 0.0 if got is None else float(got)
            except (TypeError, ValueError):
                floor = 0.0
            # The change mode's per-object rate limit: a float that jitters
            # every frame would otherwise be a "change" every frame. ZERO is
            # allowed and means real time -- the row is written the moment the
            # value moves. That is safe because app/outputs.py caps a table at
            # _MAX_DATASET_ROWS_PER_S regardless of this setting, so "no rate
            # limit" cannot become "write at frame rate for ever".
            clean["min_interval_s"] = min(3600.0, max(0.0, floor))
    elif otype == "record":
        # A clip is TWO halves: what led up to the moment, and what followed
        # it. Each is capped at 5s, so a trigger firing every frame still
        # produces bounded clips (mirrors app/capture.py MAX_CLIP_S).
        def _secs(key, default, lo=0.0):
            try:
                return min(5.0, max(lo, float(o.get(key, default) or 0)))
            except (TypeError, ValueError):
                return default

        # An output written before the split carries only `duration_s`, and it
        # meant "after the trigger" -- so that is what it becomes, and the
        # clip it produces is byte-for-byte the one it produced before.
        after_default = _secs("duration_s", 5.0, 0.5) if o.get("after_s") is None \
            else 5.0
        clean["after_s"] = _secs("after_s", after_default, 0.5)
        # Zero is the DEFAULT and means no rolling buffer at all: the run-up
        # costs a per-camera ring of rendered frames kept whether or not
        # anything ever fires, which is a real cost and not one to opt anybody
        # into silently.
        clean["before_s"] = _secs("before_s", 0.0)
        # Kept in step for anything still reading the old field (an older app
        # build on a box mid-update reads duration_s and nothing else).
        clean["duration_s"] = clean["after_s"]
    if otype in ("snapshot", "record"):
        # The page this node's photos or clips go to in Footage: its name and
        # the short form the files' names carry.
        pname = " ".join(str(o.get("page_name") or "").split())[:60]
        pslug = "".join(c for c in str(o.get("page") or "").lower()
                        if c.isascii() and c.isalnum())[:16]
        if pname and pslug:
            clean["page_name"], clean["page"] = pname, pslug
        # How OFTEN a capture output may act while its trigger keeps firing.
        # Without this a violation that lasts a minute writes hundreds of
        # near-identical frames.
        mode = str(o.get("mode") or "every").strip().lower()
        clean["mode"] = mode if mode in CAPTURE_MODES else "every"
        try:
            gap = float(o.get("interval_s") or 5)
        except (TypeError, ValueError):
            gap = 5.0
        clean["interval_s"] = min(3600.0, max(0.5, gap))
        # WHICH camera the picture comes from. Blank follows the rule that
        # fired -- one camera, or the camera a zone belongs to -- which is the
        # behaviour every capture output had before this could be set.
        cam = str(o.get("camera") or "").strip()
        if cam:
            clean["camera"] = cam[:32]
        # Stamp the object that caused THIS firing into the file and onto the
        # picture. Carried whether or not the chain can supply one: whether an
        # id is available is a runtime fact (the event either names an object
        # or it does not), so refusing it here would refuse a setting that is
        # right for tomorrow's rule.
        if o.get("oid_tag"):
            clean["oid_tag"] = True
        if otype == "snapshot":
            # Close-ups of each detected object, and the place the PDF notice
            # prints. Neither exists on a clip.
            if o.get("crop"):
                clean["crop"] = True
            if o.get("hold"):
                clean["hold"] = True
            # (the Footage page this node's captures go to is kept below,
            # for a clip as well as a photo)
            for key, cap in (("location", 120), ("violation", 120),
                             ("notice_title", 60)):
                text = " ".join(str(o.get(key) or "").split())[:cap]
                if text:
                    clean[key] = text
    return clean


# -- capture switches (System Control toggles) --------------------------------

def _read_labels(pkg_dir, path):
    if not path:
        return []
    if not os.path.isabs(path):
        path = os.path.join(pkg_dir, path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def model_labels(systems_dir, slug):
    """Every class the ACTIVE model(s) can emit, in class-index order.

    Read from the labels file(s) the detector config points at -- the same
    files the app loads -- so the checklist offered to an operator is exactly
    what the models produce, not a guess. With [[detector.models]] the union
    is returned (order-preserving, first model first). Empty when no model
    ships labels.
    """
    pkg_dir = _pkg_dir(systems_dir, slug)
    cfg = _merged(pkg_dir)
    detector = cfg.get("detector") or {}
    shared = ((detector.get("hailo") or {}).get("labels")
              or detector.get("labels") or "")
    entries = detector.get("models") or []
    if not entries:
        return _read_labels(pkg_dir, shared)
    seen, union = set(), []
    for e in entries:
        if not isinstance(e, dict):
            continue
        for label in _read_labels(pkg_dir, e.get("labels") or shared):
            if label not in seen:
                seen.add(label)
                union.append(label)
    return union


def model_labels_by_model(systems_dir, slug):
    """{model name: [labels]} for every active model -- the multi list when
    present, else the single active model under its configured name."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    cfg = _merged(pkg_dir)
    detector = cfg.get("detector") or {}
    shared = ((detector.get("hailo") or {}).get("labels")
              or detector.get("labels") or "")
    entries = detector.get("models") or []
    if not entries:
        name = ((detector.get("hailo") or {}).get("model")
                or detector.get("model") or "")
        # NAMED even when it ships no labels file. The map used to need both,
        # which quietly cost the workflow editor its Model picker on every
        # single-model system whose model has no labels beside it (a custom
        # .hef): the editor asks this for the LIST OF MODELS, and a model with
        # an empty vocabulary is still a model a Detection Stream may be
        # scoped to. Its classes are then simply unknown, which is what the
        # empty list says -- and what the class picker already handles.
        return {str(name): _read_labels(pkg_dir, shared)} if name else {}
    out = {}
    for e in entries:
        if isinstance(e, dict) and e.get("model"):
            out[str(e["model"])] = _read_labels(pkg_dir, e.get("labels") or shared)
    return out


#: The tracker's knobs, with the app engine's own defaults and the range each
#: is clamped to. Mirrors systems/demo/app/tracker.py -- duplicated on purpose,
#: the app imports nothing from core/ (same doctrine as the camera limits).
#:
#: These decide when two boxes on consecutive frames are the SAME object, which
#: is what every identity-based count rests on: unique visitors, line
#: crossings, dwell, travel and cycle time all key on the track id.
#: The knobs carry the HAILO TRACKER's exact names and defaults -- that is
#: the vocabulary anyone who has tuned a Hailo pipeline already knows, and a
#: renamed knob is a knob nobody can google. Semantics (app/tracker.py): the
#: *_thr values are distance thresholds (higher = more permissive; IoU gate
#: is 1 - thr), the keep_*_frames values are frame counts.
TRACKER_FIELDS = {
    # Centre-distance gate for fast movers whose boxes no longer overlap.
    "kalman_dist_thr": {"default": 0.8, "min": 0.05, "max": 1.0, "step": 0.05},
    # Match gate for a CONFIRMED track (distance: gate IoU = 1 - value).
    "iou_thr": {"default": 0.9, "min": 0.05, "max": 0.98, "step": 0.05},
    # Match gate while a track is still NEW -- stricter, a ghost must earn it.
    "init_iou_thr": {"default": 0.7, "min": 0.05, "max": 0.98, "step": 0.05},
    # Matched frames before a new track is CONFIRMED and gets a public id.
    "keep_new_frames": {"default": 2, "min": 1, "max": 30, "step": 1},
    # Unseen frames a confirmed track survives...
    "keep_tracked_frames": {"default": 15, "min": 1, "max": 300, "step": 1},
    # ...plus this many more in the lost state before it is gone.
    "keep_lost_frames": {"default": 2, "min": 0, "max": 300, "step": 1},
}


def tracker_params(systems_dir, slug):
    """This system's tracker settings, defaults filled in."""
    raw = (_merged(_pkg_dir(systems_dir, slug)).get("tracker") or {})
    out = {}
    for key, spec in TRACKER_FIELDS.items():
        try:
            value = float(raw.get(key, spec["default"]))
        except (TypeError, ValueError):
            value = spec["default"]
        out[key] = max(spec["min"], min(spec["max"], value))
    return out


@locked
def set_tracker_params(systems_dir, slug, updates):
    """Write the tracker knobs. Unknown keys are refused rather than ignored --
    a typo that silently does nothing is worse than an error, because the
    operator would go looking for the effect it never had."""
    if not isinstance(updates, dict):
        raise ValueError("tracker settings must be an object")
    unknown = sorted(set(updates) - set(TRACKER_FIELDS))
    if unknown:
        raise ValueError(f"unknown tracker setting(s): {', '.join(unknown)}")
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    table = dict(local.get("tracker") or {})
    for key, value in updates.items():
        spec = TRACKER_FIELDS[key]
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if not spec["min"] <= num <= spec["max"]:
            raise ValueError(
                f"{key} must be between {spec['min']} and {spec['max']}")
        table[key] = round(num, 4)
    local["tracker"] = table
    _write_local(pkg_dir, local)
    return tracker_params(systems_dir, slug)


@locked
def set_detector_params(systems_dir, slug, classes=None, confidence=None,
                        model=None):
    """Tune a RUNNING model: which classes count, and how sure it must be.

    Both belong to the detector rather than to any one model source, so this
    works the same whether the model came from the catalogue, an upload or the
    drop folder. Written to config.local.toml at BOTH the top level and the
    hailo sub-table, because the app merges the sub-table over the top level
    and a value set in only one of them can be silently overridden.

    `model` narrows it to ONE entry of a multi-model set. Without that, tuning
    the classes of one model while two run writes a filter every model obeys
    -- so ticking "pcb" on the second model silenced the first, which emits no
    such class. Each entry carries its own classes/confidence for exactly this
    reason; the shared keys stay the fallback for a single-model system.
    """
    pkg_dir = _pkg_dir(systems_dir, slug)
    # FRESH under the lock, as edit_local reads: the hub writes this file too,
    # and a same-tick, same-size write would otherwise be served from cache.
    _toml_cache.pop(_local_path(pkg_dir), None)
    local = _read_toml(_local_path(pkg_dir))
    detector = dict(local.get("detector") or {})
    hailo = dict(detector.get("hailo") or {})

    entries = [dict(e) for e in (detector.get("models") or [])
               if isinstance(e, dict)]
    target = next((e for e in entries if e.get("model") == str(model or "")), None)
    if target is not None:
        if classes is not None:
            if not isinstance(classes, list):
                raise ValueError("classes must be a list")
            target["classes"] = [str(c).strip() for c in classes
                                 if str(c).strip()][:200]
        if confidence is not None:
            try:
                conf = float(confidence)
            except (TypeError, ValueError):
                raise ValueError("confidence must be a number")
            if not 0 < conf < 1:
                raise ValueError("confidence must be between 0 and 1")
            target["confidence"] = round(conf, 3)
        detector["models"] = entries
        local["detector"] = detector
        _write_local(pkg_dir, local)
        return {"model": target["model"],
                "classes": list(target.get("classes") or []),
                "confidence": float(target.get("confidence")
                                    or (_merged(pkg_dir).get("detector") or {})
                                    .get("confidence") or 0.45)}

    if classes is not None:
        if not isinstance(classes, list):
            raise ValueError("classes must be a list")
        clean = [str(c).strip() for c in classes if str(c).strip()][:200]
        # An empty list is meaningful: "count every class". Storing it (rather
        # than dropping the key) is what makes clearing the filter stick.
        detector["classes"] = clean
        hailo["classes"] = clean
    if confidence is not None:
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            raise ValueError("confidence must be a number")
        if not 0 < conf < 1:
            raise ValueError("confidence must be between 0 and 1")
        conf = round(conf, 3)
        detector["confidence"] = conf
        hailo["confidence"] = conf

    detector["hailo"] = hailo
    local["detector"] = detector
    _write_local(pkg_dir, local)
    merged = (_merged(pkg_dir).get("detector") or {})
    return {"classes": list((merged.get("hailo") or {}).get("classes")
                            or merged.get("classes") or []),
            "confidence": float(merged.get("confidence") or 0.45)}


def storage_external(systems_dir, slug):
    """Whether this system's data lands on SECOND storage (the NVMe, or an
    explicit [app].data_dir) rather than the boot media. Captures and
    recordings are refused without it: filling the SD card the OS runs from
    is how a recorder bricks the box."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    cfg = _merged(pkg_dir)
    if (cfg.get("app") or {}).get("data_dir"):
        return True
    module = _load_app_module(pkg_dir, "config")
    try:
        return bool(module is not None and module._nvme_usable())
    except Exception:                                          # noqa: BLE001
        return False


#: Modbus server defaults. TCP on 5020, not 502: the app does not run as root
#: and ports below 1024 need it, so the documented default is one that will
#: actually bind. A deployment that wants 502 gives the process the capability
#: and sets it.
MODBUS_SERVER_DEFAULTS = {
    "enabled": False, "mode": "tcp", "host": "0.0.0.0", "port": 5020,
    "unit_id": 1, "device": "/dev/ttyUSB0", "baudrate": 9600,
    "parity": "N", "stopbits": 1, "bytesize": 8,
}

MODBUS_MODES = ("tcp", "rtu", "both")

#: Where a PLC's 502 lands: the box's nftables rule redirects 502 to this
#: port (scripts/nftables/modbus-redirect.nft). Not a setting -- a redirect
#: that followed the setting would need a root path of its own.
MODBUS_REDIRECT_PORT = 5020

#: Written by scripts/install.sh once the 502 redirect is loaded. The service
#: account cannot ask nftables, so this is how the panel knows whether to
#: say "PLCs can use 502" or "point the PLC at 5020".
MODBUS_REDIRECT_MARKER = "/etc/irivai/modbus-502-redirect"


def modbus_redirect_installed():
    return os.path.isfile(MODBUS_REDIRECT_MARKER)


def _refuse_modbus_port(port, systems_dir, slug):
    """A Modbus TCP port this box may hand the server, or ValueError naming
    who has it. Without this a `modbus.edit` holder could put the listener on
    the hub's port: harmless while the hub is up, and on the next reboot
    whichever started first won -- an app winning left the box with no
    dashboard (Pipi, 2026-10-05)."""
    from core.services import hubport, provision          # noqa: PLC0415
    if port < 1024:
        raise ValueError(
            f"ports below 1024 need root - use {MODBUS_REDIRECT_PORT}; a PLC "
            f"polling 502 reaches {MODBUS_REDIRECT_PORT} through this box's "
            "redirect")
    if port != MODBUS_REDIRECT_PORT and port in hubport.RESERVED:
        raise ValueError(f"port {port} is {hubport.RESERVED[port]} on this box")
    if port == hubport.port():
        raise ValueError(f"port {port} is this box's software port")
    owner = hubport.master_ports().get(port)
    if owner:
        raise ValueError(f"port {port} is the system \"{owner}\"'s own port")
    try:
        streams = provision.stream_ports_in_use(systems_dir)
    except Exception:                                          # noqa: BLE001
        streams = set()
    if port in streams:
        raise ValueError(f"port {port} carries a system's live video")
    try:
        servers = provision.modbus_ports_in_use(systems_dir)
    except Exception:                                          # noqa: BLE001
        servers = {}
    other = servers.get(port)
    if other and other != slug:
        raise ValueError(
            f"the system \"{other}\" already serves Modbus on port {port} - "
            "one port, one system" + (
                f" (and PLCs on 502 reach whichever holds {port})"
                if port == MODBUS_REDIRECT_PORT else ""))


def get_modbus_server(systems_dir, slug):
    """The [modbus_server] settings, defaults filled in."""
    cfg = _merged(_pkg_dir(systems_dir, slug))
    saved = cfg.get("modbus_server") or {}
    out = dict(MODBUS_SERVER_DEFAULTS)
    for key in out:
        if key in saved:
            out[key] = saved[key]
    out["enabled"] = bool(out["enabled"])
    return out


@locked
def set_modbus_server(systems_dir, slug, body):
    """Write [modbus_server] into config.local.toml. The app re-reads it on
    its ordinary config poll and restarts the listener only if something that
    needs a socket changed -- editing the register map never drops a PLC."""
    body = dict(body or {})
    cur = get_modbus_server(systems_dir, slug)
    out = dict(cur)

    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "mode" in body:
        mode = str(body["mode"] or "tcp").lower()
        if mode not in MODBUS_MODES:
            raise ValueError(f"mode must be one of {', '.join(MODBUS_MODES)}")
        out["mode"] = mode
    if "host" in body:
        out["host"] = str(body["host"] or "0.0.0.0")[:60]
    if "port" in body:
        try:
            port = int(body["port"])
        except (TypeError, ValueError):
            raise ValueError("the TCP port must be a whole number")
        if not 1 <= port <= 65535:
            raise ValueError("the TCP port must be between 1 and 65535")
        out["port"] = port
    if "unit_id" in body:
        try:
            unit = int(body["unit_id"])
        except (TypeError, ValueError):
            raise ValueError("the unit id must be a whole number")
        # 0 is broadcast and 248+ is reserved; a PLC pointed at either gets
        # no answer, which looks like a dead box rather than a bad setting.
        if not 1 <= unit <= 247:
            raise ValueError("the unit id must be between 1 and 247")
        out["unit_id"] = unit
    if "device" in body:
        out["device"] = str(body["device"] or "")[:120]
    if "baudrate" in body:
        try:
            out["baudrate"] = int(body["baudrate"])
        except (TypeError, ValueError):
            raise ValueError("the baud rate must be a whole number")
    if "parity" in body:
        parity = str(body["parity"] or "N")[:1].upper()
        if parity not in ("N", "E", "O"):
            raise ValueError("parity must be N, E or O")
        out["parity"] = parity
    if "stopbits" in body:
        out["stopbits"] = 2 if str(body["stopbits"]) == "2" else 1
    if "bytesize" in body:
        out["bytesize"] = 7 if str(body["bytesize"]) == "7" else 8

    if out["enabled"] and out["mode"] in ("tcp", "both"):
        _refuse_modbus_port(int(out["port"]), systems_dir, slug)

    # Serial is a shared, exclusive resource: two programs on one RS-485 line
    # is a bus with two masters and no owner. Refused for the same reason the
    # Board I/O page refuses a GPIO line something else drives.
    if out["enabled"] and out["mode"] in ("rtu", "both"):
        if not str(out.get("device") or "").strip():
            raise ValueError("RTU needs a serial port - choose one, or use TCP")

    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    local["modbus_server"] = out
    _write_local(pkg_dir, local)
    return out


# -- Modbus SERVERS this box talks TO (the Node-RED "modbus-client") ----------
#
# Not the section above. [modbus_server] is this box AS a device that a PLC
# polls; these are the devices this box polls and writes -- a PLC, a VFD, a
# remote I/O module. Named and SHARED: a Modbus Read and three Modbus Writes
# on one PLC are four nodes and one connection, exactly as Node-RED draws it,
# so changing the PLC's address is one edit rather than four that have to be
# kept in step. Stored per system as [[modbus_clients]] in config.local.toml,
# where the app's ordinary config poll picks it up.
#
# The field names are Node-RED's, spelled the way its editor spells them,
# because the people wiring these nodes arrive from Node-RED and a setting
# that answers to the same word is a setting they do not have to look up.

MODBUS_CLIENT_DEFAULTS = {
    "name": "", "type": "tcp",
    "host": "127.0.0.1", "port": 502, "tcp_type": "DEFAULT",
    "serial_port": "/dev/ttyACM0", "serial_type": "RTU",
    "baudrate": 9600, "databits": 8, "stopbits": 1, "parity": "none",
    "unit_id": 1, "timeout_ms": 1000,
    "reconnect_on_timeout": True, "reconnect_timeout_ms": 2000,
}
MODBUS_CLIENT_TYPES = ("tcp", "serial")
#: One option each, on purpose. Node-RED offers TELNET/TCP-RTU-BUFFERED and
#: ASCII as well; nothing on this box speaks them, and a choice that silently
#: behaves like DEFAULT is worse than no choice. The field is still stored so
#: a connection copied from a Node-RED flow round-trips.
MODBUS_TCP_TYPES = ("DEFAULT",)
MODBUS_SERIAL_TYPES = ("RTU",)
MODBUS_PARITIES = ("none", "even", "odd")
#: Enough for every PLC and drive on a line. A list longer than this is a
#: config nobody can pick from, not a real installation.
MAX_MODBUS_CLIENTS = 32
#: A serial port is a device node or a Windows COM port, nothing else. Checked
#: because the app hands this string straight to the serial driver.
_SERIAL_PORT_RE = re.compile(r"^(/dev/tty[A-Za-z0-9_.\-]{1,40}|COM\d{1,3})$")
#: Node types that pick a connection, and so hold it in use.
MODBUS_CLIENT_NODES = ("output.modbus_write", "variable.rs485")


def _clamp_int(value, lo, hi, what):
    """A whole number pulled into [lo, hi]. Not-a-number is the one thing
    refused: a timeout of 90000 means "long" and 60000 is long, but "abc" means
    nothing and guessing at it would save a connection nobody asked for."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number")
    return max(lo, min(hi, n))


def modbus_client_label(c):
    """What a connection is called in a picker: its name, else where it goes."""
    name = str(c.get("name") or "").strip()
    if name:
        return name
    if c.get("type") == "serial":
        return f"{c.get('serial_port')} @ {c.get('baudrate')}"
    return f"{c.get('host')}:{c.get('port')}"


def clean_modbus_client(body, current=None):
    """One connection, validated, defaults filled, ranges clamped.

    `current` is the stored version when this is an edit, so a partial body
    changes only what it names -- the same contract set_modbus_server keeps.
    """
    body = dict(body or {})
    out = dict(MODBUS_CLIENT_DEFAULTS)
    out.update({k: v for k, v in (current or {}).items() if k in out})
    for key in MODBUS_CLIENT_DEFAULTS:
        if key in body and body[key] is not None:
            out[key] = body[key]

    out["name"] = str(out["name"] or "").strip()[:60]
    ctype = str(out["type"] or "tcp").strip().lower()
    if ctype not in MODBUS_CLIENT_TYPES:
        raise ValueError("type must be TCP or RTU")
    out["type"] = ctype

    out["host"] = str(out["host"] or "").strip()[:120] or "127.0.0.1"
    if any(ch.isspace() for ch in out["host"]):
        raise ValueError("the host must be an address or a name, with no spaces")
    out["port"] = _clamp_int(out["port"], 1, 65535, "the port")
    out["tcp_type"] = "DEFAULT"

    port = str(out["serial_port"] or "").strip()
    if not _SERIAL_PORT_RE.match(port):
        raise ValueError("the serial port must be a /dev/tty... device "
                         "(e.g. /dev/ttyACM0) or a COM port")
    out["serial_port"] = port
    out["serial_type"] = "RTU"
    out["baudrate"] = _clamp_int(out["baudrate"], 300, 921600, "the baud rate")
    out["databits"] = _clamp_int(out["databits"], 5, 8, "the data bits")
    out["stopbits"] = 2 if str(out["stopbits"]).strip() == "2" else 1
    parity = str(out["parity"] or "none").strip().lower()
    # N/E/O too: the box's own server stores parity that way, and a value
    # pasted from there must mean the same thing here.
    parity = {"n": "none", "e": "even", "o": "odd"}.get(parity, parity)
    if parity not in MODBUS_PARITIES:
        raise ValueError("parity must be none, even or odd")
    out["parity"] = parity

    # 0..255, not the 1..247 the box's own server insists on: a TCP gateway
    # commonly answers on 0 or 255, and this end only has to ASK for it.
    out["unit_id"] = _clamp_int(out["unit_id"], 0, 255, "the Unit-Id")
    out["timeout_ms"] = _clamp_int(out["timeout_ms"], 100, 60000, "the timeout")
    out["reconnect_on_timeout"] = bool(out["reconnect_on_timeout"])
    out["reconnect_timeout_ms"] = _clamp_int(out["reconnect_timeout_ms"],
                                             100, 60000, "the reconnect timeout")
    # The Drive watchdog switch: absent unless set (modbus_watchdog).
    return modbus_watchdog.clean(body, current, out)


def list_modbus_clients(systems_dir, slug):
    """Every saved connection, cleaned, each with its picker label.

    A row that no longer validates (hand-edited config) is still LISTED, with
    defaults where it was wrong -- dropping it would strand every node that
    names it with an error that points at nothing.
    """
    cfg = _merged(_pkg_dir(systems_dir, slug))
    out = []
    for row in cfg.get("modbus_clients") or []:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        try:
            clean = clean_modbus_client(row)
        except ValueError:
            clean = clean_modbus_client({k: v for k, v in row.items()
                                         if k in ("name", "type")})
        clean["id"] = str(row["id"]).strip()[:40]
        clean["label"] = modbus_client_label(clean)
        out.append(clean)
    return out


def modbus_client_refs(slug, client_id):
    """[{"node", "label"}] -- every workflow node that picks this connection.

    Imported lazily: the workflow service imports this module, and the store
    is only needed on the one path that deletes a connection.
    """
    from core.services import workflow as workflow_service   # noqa: PLC0415
    try:
        store = workflow_service.load_store(slug) or {}
    except Exception:                                          # noqa: BLE001
        return []
    refs = []
    for w in store.get("workflows") or []:
        for n in ((w.get("graph") or {}).get("nodes") or []):
            if n.get("type") not in MODBUS_CLIENT_NODES:
                continue
            d = n.get("data") or {}
            if str(d.get("server") or "") != str(client_id):
                continue
            spec = workflow_service.REGISTRY.get(n["type"]) or {}
            refs.append({"node": n.get("id"),
                         "label": str(d.get("name") or spec.get("label")
                                      or n["type"])})
    return refs


@locked
def save_modbus_client(systems_dir, slug, body):
    """Add a connection (no id) or change one (its id). Returns it, cleaned."""
    body = dict(body or {})
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    rows = [dict(r) for r in (local.get("modbus_clients") or [])
            if isinstance(r, dict)]
    cid = str(body.get("id") or "").strip()
    if cid:
        at = next((i for i, r in enumerate(rows)
                   if str(r.get("id") or "") == cid), None)
        if at is None:
            raise LookupError("no Modbus server with that id - it may have "
                              "been deleted; reload and pick again")
        clean = clean_modbus_client(body, rows[at])
        clean["id"] = cid
        rows[at] = clean
    else:
        if len(rows) >= MAX_MODBUS_CLIENTS:
            raise ValueError(f"at most {MAX_MODBUS_CLIENTS} Modbus servers "
                             "per system")
        clean = clean_modbus_client(body)
        taken = {str(r.get("id") or "") for r in rows}
        cid = "mb_" + os.urandom(4).hex()
        while cid in taken:
            cid = "mb_" + os.urandom(4).hex()
        clean["id"] = cid
        rows.append(clean)
    local["modbus_clients"] = rows
    _write_local(pkg_dir, local)
    return {**clean, "label": modbus_client_label(clean)}


@locked
def delete_modbus_client(systems_dir, slug, client_id):
    """Remove a connection nothing uses. One a node still picks is REFUSED,
    naming the nodes -- deleting it would turn each of them into a compile
    error about a server that is simply gone, with no trail back to why."""
    cid = str(client_id or "").strip()
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    rows = [dict(r) for r in (local.get("modbus_clients") or [])
            if isinstance(r, dict)]
    if not any(str(r.get("id") or "") == cid for r in rows):
        raise LookupError("no Modbus server with that id")
    refs = modbus_client_refs(slug, cid)
    if refs:
        names = ", ".join(sorted({r["label"] for r in refs}))
        raise ValueError(f"this Modbus server is used by {len(refs)} "
                         f"node{'s' if len(refs) != 1 else ''} ({names}) - "
                         "pick another server on "
                         f"{'them' if len(refs) != 1 else 'it'} first")
    local["modbus_clients"] = [r for r in rows if str(r.get("id") or "") != cid]
    _write_local(pkg_dir, local)
    return True


def get_capture(systems_dir, slug):
    """The [capture] switches: {"snap": bool, "record": bool}.

    `snap` governs EVERY image this system writes -- the evidence frame saved
    with each event AND the workflow's Snapshot node. It defaults ON because
    evidence frames have always been saved; the switch exists so an operator
    can stop them, and turning it off must actually stop them (it used to gate
    only the workflow node, so the toggle looked broken).

    `record` defaults OFF: nothing ever records video unless asked to.
    """
    cfg = _merged(_pkg_dir(systems_dir, slug))
    cap = cfg.get("capture") or {}
    return {"snap": bool(cap.get("snap", True)),
            "record": bool(cap.get("record", False))}


@locked
def set_capture(systems_dir, slug, snap=None, record=None):
    """Write the [capture] switches into config.local.toml; the app's hooks
    pick them up on the next config poll (~2s)."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    cap = dict(local.get("capture") or {})
    if snap is not None:
        cap["snap"] = bool(snap)
    if record is not None:
        cap["record"] = bool(record)
    local["capture"] = cap
    _write_local(pkg_dir, local)
    return {"snap": bool(cap.get("snap", False)),
            "record": bool(cap.get("record", False))}


#: The rows of the status box burned into the top-right of every live frame,
#: and what each is called on the Camera Setup page. Order is the order they
#: are drawn, so this list is also the layout.
#:
#: They are stamped into the JPEG by the app rather than drawn over it by the
#: page, which is what makes them appear on EVERY viewer -- the Camera Setup
#: feed, the hub's Live Preview, a saved clip, a browser pointed straight at
#: the stream URL. It is also why hiding one has to be a config switch the app
#: reads: there is no overlay for a page to hide.
HUD_ROWS = (
    ("model", "Model"),
    ("camera", "Camera"),
    ("hailo_fps", "Hailo FPS"),
    ("camera_fps", "Camera FPS"),
)


def get_hud(systems_dir, slug):
    """Which HUD rows this system draws. Every one defaults ON -- the box has
    always shown all four, and a switch nobody has touched must not change
    what is on screen."""
    cfg = _merged(_pkg_dir(systems_dir, slug))
    hud = cfg.get("hud") or {}
    return {key: bool(hud.get(key, True)) for key, _label in HUD_ROWS}


@locked
def set_hud(systems_dir, slug, values):
    """Write the [hud] switches into config.local.toml; the app picks them up
    on the next config poll (~2s). Unknown keys are refused rather than
    written: a typo would otherwise sit in the file looking like a setting."""
    unknown = sorted(set(values or {}) - {key for key, _ in HUD_ROWS})
    if unknown:
        raise ValueError(f"unknown HUD rows: {', '.join(unknown)}")
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    hud = dict(local.get("hud") or {})
    for key, value in (values or {}).items():
        hud[key] = bool(value)
    local["hud"] = hud
    _write_local(pkg_dir, local)
    return get_hud(systems_dir, slug)


#: The setup tour, in the order the steps have to happen: a camera without a
#: model detects nothing. Zones are drawn ON the Camera Setup page now (its
#: Zones tab), so they are part of the detection step rather than their own.
# The order a new system is set up in; the Dashboard and Analytics builders come
# last, once there is a pipeline to put on them.
TUTORIAL_STEPS = ("models", "detection", "database", "workflow", "dashboard", "analytics")

#: Steps that existed in older tours, mapped to where that work lives now --
#: a board mid-tour on a retired step must resume there, not start over.
_TUTORIAL_STEP_ALIASES = {"zones": "workflow"}


def get_tutorial(systems_dir, slug):
    """{done, step} -- how far through the guided setup this system is.

    Stored per SYSTEM rather than per browser: whoever set the system up is
    often not whoever opens it next, and "have I already been walked through
    this board" is a fact about the board.
    """
    cfg = _merged(_pkg_dir(systems_dir, slug))
    ui = cfg.get("ui") or {}
    step = str(ui.get("tutorial_step") or TUTORIAL_STEPS[0])
    step = _TUTORIAL_STEP_ALIASES.get(step, step)
    # `fresh`: the guide has never moved on this system -- a system created just now,
    # including one made under the name of a system that was removed. The browser
    # remembers which step it folded and whether it greeted, by system NAME, so it
    # forgets all of that when this is true.
    return {"done": bool(ui.get("tutorial_done", False)),
            "step": step if step in TUTORIAL_STEPS else TUTORIAL_STEPS[0],
            "steps": list(TUTORIAL_STEPS),
            "fresh": not ui.get("tutorial_step") and "tutorial_done" not in ui}


@locked
def set_tutorial(systems_dir, slug, done=None, step=None):
    """Advance or end the tour. Ending it is one write and irreversible from
    the UI's point of view -- which is the point of a Skip button."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    ui = dict(local.get("ui") or {})
    if done is not None:
        ui["tutorial_done"] = bool(done)
    if step is not None:
        if step not in TUTORIAL_STEPS:
            raise ValueError(f"step must be one of {', '.join(TUTORIAL_STEPS)}")
        ui["tutorial_step"] = step
    local["ui"] = ui
    _write_local(pkg_dir, local)
    return get_tutorial(systems_dir, slug)


def list_media(systems_dir, slug):
    from core.services import media as media_service        # noqa: PLC0415
    pkg_dir = _pkg_dir(systems_dir, slug)
    return media_service.listing(_data_dir(pkg_dir, _merged(pkg_dir)))


def save_media(systems_dir, slug, filename, blob):
    from core.services import media as media_service        # noqa: PLC0415
    pkg_dir = _pkg_dir(systems_dir, slug)
    return media_service.save(_data_dir(pkg_dir, _merged(pkg_dir)),
                              filename, blob)


def delete_media(systems_dir, slug, name):
    from core.services import media as media_service        # noqa: PLC0415
    pkg_dir = _pkg_dir(systems_dir, slug)
    return media_service.delete(_data_dir(pkg_dir, _merged(pkg_dir)), name)


def media_file(systems_dir, slug, name):
    """Absolute path of one stored file, or None."""
    from core.services import media as media_service        # noqa: PLC0415
    pkg_dir = _pkg_dir(systems_dir, slug)
    return media_service.media_path(_data_dir(pkg_dir, _merged(pkg_dir)), name)


def _capture_cameras(pkg_dir):
    """[(sanitised tag, camera id, camera name)] for one system, longest tag
    first.

    capture.py names its files `cap_<camera>_..._<stamp>` and
    `clip_<camera>_..._<stamp>.mp4`, with the camera id run through the same
    sanitiser every time (non-alphanumerics become "_", 24 characters). That
    is LOSSY -- "line-a" and "line_a" write the same prefix -- so a file is
    matched back against the system's own camera list rather than parsed. A
    tag that matches two cameras is claimed by neither; longest first so
    "gate" never steals a file belonging to "gate_2".

    Read straight off the merged config: this runs on every Footage poll, and
    cameras() rescans the USB bus to freshen device names, which is a bus scan
    nobody asked for to answer "which camera saved this".
    """
    out = []
    for cam in _camera_list(_merged(pkg_dir)) or []:
        cid = str(cam.get("id") or "")
        if not cid:
            continue
        tag = "".join(c if c.isalnum() else "_" for c in cid).strip("_")[:24]
        if tag:
            out.append((tag, cid, str(cam.get("name") or cid)))
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out


def _tagged_camera(stem, cams):
    """Which camera wrote this capture file, as (id, name). ("", "") when the
    name predates per-camera tagging or names a camera that is gone -- the
    Footage page shows those together rather than inventing an owner."""
    for tag, cid, name in cams:
        if stem.startswith(tag + "_") or stem == tag:
            # Ambiguous prefixes are not attributed: two cameras whose ids
            # sanitise the same way would otherwise both answer to one file.
            if sum(1 for t, _c, _n in cams if t == tag) == 1:
                return cid, name
            return "", ""
    return "", ""


def list_clips(systems_dir, slug, limit=50):
    """Recorded clips, newest first: [{name, bytes, mtime, camera,
    camera_name}]."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    clips_dir = os.path.join(_data_dir(pkg_dir, _merged(pkg_dir)), "clips")
    cams = _capture_cameras(pkg_dir)
    out = []
    try:
        for name in os.listdir(clips_dir):
            if not name.endswith(".mp4"):
                continue
            path = os.path.join(clips_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            stem = name[:-4]
            cid, cname = _tagged_camera(
                stem[5:] if stem.startswith("clip_") else stem, cams)
            pg = _PAGE_RE.search(stem)
            out.append({"name": name, "bytes": st.st_size,
                        "mtime": int(st.st_mtime),
                        "camera": cid, "camera_name": cname,
                        # the Record Clip node's page (`..._pg<slug>_...`)
                        "page": pg.group(1) if pg else ""})
    except OSError:
        return []
    out.sort(key=lambda c: c["mtime"], reverse=True)
    return out[:limit] if limit and limit > 0 else out


def delete_clips(systems_dir, slug, names):
    """Delete recorded clips. Returns how many went; clip_path bounds every
    delete to this system's clips/ folder."""
    removed = 0
    for name in names or []:
        path = clip_path(systems_dir, slug, str(name))
        if not path:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            continue
        # The poster frame goes with it. Left behind, a clip later recorded
        # under a name that happened to repeat would show the old picture --
        # and a folder of thumbnails for clips nobody has is just disk.
        try:
            os.remove(os.path.join(os.path.dirname(path), ".thumbs",
                                   os.path.basename(path) + ".jpg"))
        except OSError:
            pass
    return removed


def clip_thumb(systems_dir, slug, name, width=360):
    """A poster frame for one clip, generated on first request and cached
    beside it (clips/.thumbs/). Returns a path, or None.

    Footage drew every clip as a black rectangle with a play triangle on it,
    which is the same picture for all of them -- so a page of thirty clips
    said nothing about which one held the thing you were looking for, and the
    only way to find out was to open each in turn. A poster frame is the whole
    difference between a list and a contact sheet.

    Not the FIRST frame: a clip that carries run-up opens on the seconds
    before anything happened, and the frame at the trigger is the one worth
    showing. A quarter of the way in is a cheap approximation of "after the
    lead-in, before it is over" that needs no knowledge of how the clip was
    armed. Falls back to the first readable frame on anything shorter.

    Returns None rather than raising when OpenCV is missing or the file is
    still being written -- the caller draws its placeholder, which is what the
    grid did for every clip before this existed.
    """
    src = clip_path(systems_dir, slug, name)
    if not src:
        return None
    folder = os.path.join(os.path.dirname(src), ".thumbs")
    dst = os.path.join(folder, name + ".jpg")
    try:
        if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst
        import cv2                                             # noqa: PLC0415
        cap = cv2.VideoCapture(src)
        try:
            if not cap.isOpened():
                return None
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frames > 4:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frames // 4)
            ok, img = cap.read()
            if not ok or img is None:
                # The seek failed (a clip still being written has no reliable
                # frame count), so take whatever the head of the file gives.
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, img = cap.read()
            if not ok or img is None:
                return None
        finally:
            cap.release()
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, max(1, int(h * width / w))),
                             interpolation=cv2.INTER_AREA)
        os.makedirs(folder, exist_ok=True)
        # imwrite picks its codec from the EXTENSION, so the temp name has to
        # end in .jpg -- ".tmp" alone has no writer and raises.
        tmp = dst + f".{os.getpid()}.tmp.jpg"
        if not cv2.imwrite(tmp, img, [int(cv2.IMWRITE_JPEG_QUALITY), 78]):
            return None
        os.replace(tmp, dst)
        return dst
    except Exception:                                          # noqa: BLE001
        return None


def clip_path(systems_dir, slug, name):
    """Absolute path of one clip, or None. The basename check is the security
    boundary -- a name with separators must never escape clips/."""
    if os.path.basename(name) != name or not name.endswith(".mp4"):
        return None
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir(pkg_dir, _merged(pkg_dir)), "clips", name)
    return path if os.path.exists(path) else None


def _validate_command(c):
    """One stored Telegram command, validated. Raises ValueError.

    Written by the compiler, read by the master's poller -- one route writes,
    another reads, so this boundary checks rather than trusts. The statements
    matter most: they run on a stranger's tap, months after anybody looked at
    the canvas.
    """
    from core.services import telegram_commands as tg  # noqa: PLC0415
    if not isinstance(c, dict):
        raise ValueError("a command is an object")
    word = tg.clean_command(c.get("command"))
    if not word:
        raise ValueError("a command needs a word people can type")
    buttons = []
    for b in (c.get("buttons") if isinstance(c.get("buttons"), list) else [])[:tg.MAX_BUTTONS]:
        if not isinstance(b, dict):
            continue
        period = str(b.get("period") or "today").strip().lower()
        period = period if period in tg.PERIODS else "today"
        if str(b.get("kind") or "") == "photos":
            # A PHOTO button carries no statement: it reads the snapshots this
            # system already saved. The window ceiling is checked again here,
            # because this document is read by the poller months after the
            # canvas that wrote it was last opened.
            from core.services import telegram_photos as ph   # noqa: PLC0415
            if not tg.album_period_ok(period):
                raise ValueError("a picture button covers at most "
                                 f"{tg.MAX_ALBUM_HOURS} hours")
            try:
                most = int(b.get("most", ph.MAX_ALBUM))
            except (TypeError, ValueError):
                most = ph.MAX_ALBUM
            buttons.append({"label": str(b.get("label") or "")[:32] or "Photos",
                            "period": period, "kind": "photos",
                            "camera": str(b.get("camera") or "")[:60],
                            # 0 means ALL, up to the box's own ceiling.
                            "most": (ph.ALL if most <= 0
                                     else min(ph.MAX_ALL, most))})
            continue
        sql = str(b.get("sql") or "").strip().rstrip(";").strip()
        if not sql:
            continue
        # The same wall the console and the CSV node enforce, enforced again
        # here: a button is a statement a person on the far side of the
        # internet can run whenever they like.
        if ";" in sql or not re.match(r"^\s*(select|with)\b", sql, re.I):
            raise ValueError("a command button may only SELECT - it reads the "
                             "database, it never changes it")
        tables = []
        for t in (b.get("tables") if isinstance(b.get("tables"), list) else [])[:12]:
            key = str((t or {}).get("key") or "").strip()
            if key:
                tables.append({"key": key[:60],
                               "name": str(t.get("name") or key)[:60],
                               "columns": [dict(col) for col
                                           in (t.get("columns") or [])[:60]]})
        if not tables:
            raise ValueError("a command button needs the tables its Query may read")
        chart = str(b.get("chart") or "table").strip().lower()
        buttons.append({"label": str(b.get("label") or "")[:32] or "Report",
                        "period": period, "kind": "query",
                        "chart": chart if chart in tg.CHART_KINDS else "table",
                        "sql": sql[:4000], "tables": tables})
    if not buttons:
        raise ValueError("a command with no usable buttons answers nothing")
    return {"id": str(c.get("id") or "")[:40] or f"wf_{word}",
            "command": word,
            "title": str(c.get("title") or "")[:60] or word.title(),
            "intro": str(c.get("intro") or "")[:400],
            "buttons": buttons}


def get_commands(systems_dir, slug):
    """Every Telegram command this system answers. A malformed one is skipped,
    never a 500 -- one bad command must not silence the whole bot."""
    cfg = _merged(_pkg_dir(systems_dir, slug))
    out = []
    for c in cfg.get("commands") or []:
        try:
            out.append(_validate_command(c))
        except (ValueError, TypeError):
            continue
    return out


@locked
def set_commands(systems_dir, slug, commands):
    """Replace [[commands]] in config.local.toml. The poller re-reads them each
    time it answers, so a canvas saved now is answered now."""
    if not isinstance(commands, list):
        raise ValueError("commands must be a list")
    cleaned = [_validate_command(c) for c in commands]
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    if cleaned:
        local["commands"] = cleaned
    else:
        local.pop("commands", None)
    _write_local(pkg_dir, local)
    return cleaned


def _validate_watch(w):
    """One stored threshold watch, validated. Raises ValueError.

    The compiler writes these and the master's timer reads them, so this
    boundary checks rather than trusts -- and the statement matters most: it
    runs unattended, every few minutes, for as long as the node exists.
    """
    from core.services import alert_watch as aw               # noqa: PLC0415
    from core.services import telegram_commands as tg         # noqa: PLC0415
    if not isinstance(w, dict):
        raise ValueError("a watch is an object")
    sql = str(w.get("sql") or "").strip().rstrip(";").strip()
    # The same wall the console, the CSV node and a command button enforce.
    if ";" in sql or not re.match(r"^\s*(select|with)\b", sql, re.I):
        raise ValueError("a watch may only SELECT - it reads the database, it "
                         "never changes it")
    tables = []
    for t in (w.get("tables") if isinstance(w.get("tables"), list) else [])[:12]:
        key = str((t or {}).get("key") or "").strip()
        if key:
            tables.append({"key": key[:60],
                           "name": str(t.get("name") or key)[:60],
                           "columns": [dict(col) for col
                                       in (t.get("columns") or [])[:60]]})
    if not tables:
        raise ValueError("a watch needs the tables its Query may read")
    sends = []
    for send in (w.get("sends") if isinstance(w.get("sends"), list) else [])[:8]:
        if not isinstance(send, dict):
            continue
        kind = str(send.get("kind") or "").strip().lower()
        if kind == "telegram":
            sends.append({"kind": "telegram",
                          "chat_id": str(send.get("chat_id") or "")[:40],
                          "message": str(send.get("message") or "")[:1200]})
        elif kind == "email":
            to = str(send.get("to") or "").strip()
            if not to:
                continue
            sends.append({"kind": "email", "to": to[:400],
                          "account": str(send.get("account") or "")[:40],
                          "subject": str(send.get("subject") or "")[:200],
                          "body": str(send.get("body") or "")[:2000]})
    if not sends:
        raise ValueError("a watch with nowhere to send reports nothing")
    cfg = aw.clean_watch(w)
    period = cfg["period"]
    return {"id": str(w.get("id") or "")[:40] or "watch",
            **cfg,
            # `custom` means "ask somebody for dates", which a timer cannot do.
            "period": period if period in tg.PERIODS and period != "custom"
                      else "today",
            "sql": sql[:4000], "tables": tables, "sends": sends}


def get_watches(systems_dir, slug):
    """Every threshold watch this system runs. A malformed one is skipped,
    never a 500 -- one bad watch must not stop the others firing."""
    from core.services import alert_watch as aw               # noqa: PLC0415
    cfg = _merged(_pkg_dir(systems_dir, slug))
    out = []
    for w in (cfg.get("watches") or [])[:aw.MAX_WATCHES]:
        try:
            out.append(_validate_watch(w))
        except (ValueError, TypeError):
            continue
    return out


@locked
def set_watches(systems_dir, slug, watches):
    """Replace [[watches]] in config.local.toml. The timer re-reads them each
    tick, so a canvas saved now is watched now."""
    from core.services import alert_watch as aw               # noqa: PLC0415
    if not isinstance(watches, list):
        raise ValueError("watches must be a list")
    cleaned = [_validate_watch(w) for w in watches[:aw.MAX_WATCHES]]
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    if cleaned:
        local["watches"] = cleaned
    else:
        local.pop("watches", None)
    _write_local(pkg_dir, local)
    return cleaned


def get_preprocess(systems_dir, slug):
    """camera id -> {steps, preview} for every camera with a chain on it.

    The chain lives ON the camera entry, beside `controls` and `measure`, for
    the same reason those do: it is a property of that camera's picture, and
    the app reads it off the camera it is opening (app/preprocess.py, called
    from run.py with pipe.cfg["preprocess"]).
    """
    out = {}
    for c in cameras(systems_dir, slug):
        steps = _clean_preprocess(c.get("preprocess"))
        if steps:
            out[c["id"]] = {"steps": steps,
                            "preview": bool(c.get("preprocess_preview"))}
    return out


@locked
def set_preprocess(systems_dir, slug, wanted, config_dir=None):
    """Put each camera's picture chain on its entry. `wanted` is
    {camera id: {steps, preview}}; a camera not named loses its chain.

    Whole-list write underneath, because that is the only shape config
    merging supports (see set_cameras) -- and NOTHING is written when nothing
    changed. A workflow save must not rewrite the camera list of every system
    that has no Pre-process node in it: that write can legitimately fail on a
    camera budget somebody is already over, and a save that trips on a rule
    about a feature the graph does not use is a save that reads as broken.
    """
    if not isinstance(wanted, dict):
        raise ValueError("preprocess must be a mapping of camera -> chain")
    entries = [dict(c) for c in cameras(systems_dir, slug)]
    touched = False
    for e in entries:
        chain = wanted.get(e["id"]) or {}
        steps = _clean_preprocess(chain.get("steps"))
        preview = bool(chain.get("preview")) if steps else False
        had = _clean_preprocess(e.get("preprocess"))
        if steps == had and preview == bool(e.get("preprocess_preview")):
            continue
        touched = True
        if steps:
            e["preprocess"] = steps
            e["preprocess_preview"] = preview
        else:
            # Absent means "show the detector the camera's own picture", so a
            # deleted node CLEARS the keys rather than leaving an empty list:
            # a camera that never had a chain and one whose chain was removed
            # have to reach the app as the same camera.
            e.pop("preprocess", None)
            e.pop("preprocess_preview", None)
    if not touched:
        return get_preprocess(systems_dir, slug)
    set_cameras(systems_dir, slug, entries, config_dir=config_dir)
    return get_preprocess(systems_dir, slug)


#: What may start a snapshot, and the most frames one may take (mirrors
#: workflow.MAX_SNAP_FRAMES; the app clamps again).
SNAPSHOT_KINDS = ("inject", "trigger", "level")
MAX_SNAPSHOT_FRAMES = 600


def _clean_snapshot(raw):
    """One camera's snapshot config, sanitised for storage. {} when none.

    Shape only, like _clean_preprocess: the compiler decides WHAT starts a
    snapshot and the app decides how it runs; this keeps the config file
    readable and bounded. `key` is the stat the app watches (always an
    fn_<id>_<metric> name), `kind` says how it is reset when the frames are
    taken, `frames` how many frames to detect.
    """
    if not isinstance(raw, dict):
        return {}
    out = []
    for src in (raw.get("sources") or [])[:8]:
        if not isinstance(src, dict):
            continue
        kind = str(src.get("kind") or "").strip()
        key = str(src.get("key") or "").strip()
        if kind not in SNAPSHOT_KINDS or not re.fullmatch(r"fn_[A-Za-z0-9_]{1,100}", key):
            continue
        try:
            frames = int(src.get("frames") or 1)
        except (TypeError, ValueError):
            frames = 1
        out.append({"kind": kind, "key": key,
                    "fn": str(src.get("fn") or "")[:60],
                    "frames": max(1, min(MAX_SNAPSHOT_FRAMES, frames))})
    if not out:
        return {}
    # What the camera does between snaps; absent = decided by the source.
    idle = raw.get("idle")
    return {"sources": out, **({"idle": idle} if idle in ("close", "keep") else {})}


def get_snapshot(systems_dir, slug):
    """camera id -> {sources} for every camera set to detect on snapshots."""
    out = {}
    for c in cameras(systems_dir, slug):
        cfg = _clean_snapshot(c.get("snapshot"))
        if cfg:
            out[c["id"]] = cfg
    return out


@locked
def set_snapshot(systems_dir, slug, wanted, config_dir=None):
    """Put each camera's snapshot config on its entry. `wanted` is
    {camera id: {sources}}; a camera not named goes back to every frame.

    The same shape and the same rule as set_preprocess: NOTHING is written
    when nothing changed, so saving a workflow with no snapshot stream does
    not rewrite any system's camera list.
    """
    if not isinstance(wanted, dict):
        raise ValueError("snapshot must be a mapping of camera -> config")
    entries = [dict(c) for c in cameras(systems_dir, slug)]
    touched = False
    for e in entries:
        cfg = _clean_snapshot(wanted.get(e["id"]))
        had = _clean_snapshot(e.get("snapshot"))
        if cfg == had:
            continue
        touched = True
        if cfg:
            e["snapshot"] = cfg
        else:
            e.pop("snapshot", None)
    if not touched:
        return get_snapshot(systems_dir, slug)
    set_cameras(systems_dir, slug, entries, config_dir=config_dir)
    return get_snapshot(systems_dir, slug)


def get_outputs(systems_dir, slug):
    cfg = _merged(_pkg_dir(systems_dir, slug))
    out = []
    for o in cfg.get("outputs") or []:
        try:
            out.append(_validate_output(o))
        except (ValueError, TypeError):
            continue
    return out


@locked
def set_outputs(systems_dir, slug, outputs):
    """Replace [[outputs]] in config.local.toml. The running app re-reads them on
    the next config change and fires enabled ones on matching events."""
    if not isinstance(outputs, list):
        raise ValueError("outputs must be a list")
    cleaned = [_validate_output(o) for o in outputs]
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    local["outputs"] = cleaned
    _write_local(pkg_dir, local)
    return cleaned


#: The zone editor's hint file, and the ceiling the app clamps it to anyway
#: (systems/demo/app/run.py OVERLAY_HINT_*). Seconds, not a flag: the writer is
#: a browser tab, and a closed laptop must not leave a feed without its zones.
OVERLAY_HINT_FILE = "overlay-hint.json"
OVERLAY_HINT_MAX_S = 30.0


def hide_zone_overlay(systems_dir, slug, seconds, cameras=()):
    """Ask this system's app to stop drawing zone outlines for `seconds`.

    Deliberately NOT @locked: that lock serialises config writes, and this
    writes a throwaway file in the data directory several times a minute
    while somebody drags a shape. Queueing those behind a config save would
    make the editor wait on the very thing it is trying to stay out of.

    Returns the deadline written (0 = cleared). Runtime only: no config is
    touched, so nothing here can change what the app COUNTS -- it is the
    picture, and only for as long as the editor keeps asking.
    """
    try:
        seconds = float(seconds or 0)
    except (TypeError, ValueError):
        raise ValueError("seconds must be a number")
    seconds = max(0.0, min(OVERLAY_HINT_MAX_S, seconds))
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir_cached(pkg_dir), OVERLAY_HINT_FILE)
    until = (time.time() + seconds) if seconds else 0.0
    payload = {"hide_zones_until": until,
               "cameras": [str(c) for c in (cameras or ()) if str(c).strip()]}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Whole-file rewrite, not a delete-and-create: the app watches the
        # mtime, and a file that briefly does not exist is a file the app
        # reads as "no hint" for one poll -- one frame of the outline
        # flickering back mid-drag.
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        # A read-only or missing data directory is not a reason to fail a
        # save: the worst case is the operator sees both outlines, which is
        # exactly how it worked before this existed.
        return 0.0
    return until


#: Where a press of a canvas Inject button is handed to the running app
#: (systems/demo/app/functions.py INJECT_FILE, which reads it).
INJECT_FILE = "inject-requests.json"


def request_inject(systems_dir, slug, fn_id, action="press"):
    """Press one Inject on the running app. Returns the press's sequence.

    `action` is press (inject once), or start / stop for a repeating one --
    the same file, the same freshness rule, one latest request per Inject.

    The same channel the zone editor's overlay hint uses: a small file in the
    app's data directory that the detection loop stats on its own short clock.
    A file rather than a socket because the app is standalone and has no
    command port, and because a press must survive nothing -- the app fires a
    press only while it is fresh, so one left behind by a restart is ignored.

    Runtime only, like the overlay hint: no config is touched, so a press can
    change what the flow DOES this once and never what it is.
    """
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir_cached(pkg_dir), INJECT_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            presses = json.load(f) or {}
        if not isinstance(presses, dict):
            presses = {}
    except (OSError, ValueError):
        presses = {}
    prev = presses.get(fn_id) if isinstance(presses.get(fn_id), dict) else {}
    seq = int(prev.get("seq") or 0) + 1
    presses[fn_id] = {"seq": seq, "ts": time.time(),
                      "action": action if action in ("press", "start", "stop")
                      else "press"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Written to a sibling and moved: the app must never read half a file
    # and take a press as missing.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(presses, f)
    os.replace(tmp, path)
    return seq


#: The Unit-Id scan of a Modbus Server. Same channel as an Inject press: the
#: master writes a small request file, the running app (the process that owns
#: the bus) runs the scan and keeps the result file current. File names match
#: systems/*/app/functions.py.
MODBUS_SCAN_REQUEST = "modbus-scan-request.json"
MODBUS_SCAN_RESULT = "modbus-scan-result.json"


def request_modbus_scan(systems_dir, slug, server, action="start", first=1, last=247):
    """Start (or stop) a Unit-Id scan of one Modbus Server. Returns its seq."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir_cached(pkg_dir), MODBUS_SCAN_REQUEST)
    try:
        with open(path, "r", encoding="utf-8") as f:
            prev = json.load(f) or {}
    except (OSError, ValueError):
        prev = {}
    seq = int((prev or {}).get("seq") or 0) + (0 if action == "stop" else 1)
    doc = {"seq": seq, "ts": time.time(), "server": str(server or "")[:40],
           "action": "stop" if action == "stop" else "start",
           "first": int(first), "last": int(last)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)
    return seq


def modbus_scan_result(systems_dir, slug):
    """The app's latest scan result ({seq, server, state, at, found, ...}), or
    {} when no scan has run."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir_cached(pkg_dir), MODBUS_SCAN_RESULT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f) or {}
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


@locked
def set_zones(systems_dir, slug, zones):
    """Replace [[zones]] in config.local.toml with the given list. An empty list
    clears them. The running app re-parses zones on the next config change."""
    if not isinstance(zones, list):
        raise ValueError("zones must be a list")
    cams = cameras(systems_dir, slug)
    known = {c["id"] for c in cams}
    default_cam = cams[0]["id"] if cams else MAIN_CAMERA
    cleaned = [_validate_zone(z, default_cam, known) for z in zones]
    ids = [z["id"] for z in cleaned]
    if len(ids) != len(set(ids)):
        raise ValueError("two zones share an id; each must be unique")
    pkg_dir = _pkg_dir(systems_dir, slug)
    local = _read_toml(_local_path(pkg_dir))
    local["zones"] = cleaned
    _write_local(pkg_dir, local)
    return cleaned


# Dataset capture (training snapshots and clips on the NVMe) lives in
# dataset_capture.py and dataset_record.py; re-exported so callers of
# detector_control.dataset_* are unchanged.
from core.services.dataset_capture import (  # noqa: E402,F401
    DATASET_IMG_DIR, DATASET_REC_DIR, DATASET_ROOT, MAX_DATASET_CLIP_S,
    MAX_DATASET_SESSION_S, _dataset_jobs, _grab_frame, _safe_name,
    dataset_available, dataset_delete, dataset_dir, dataset_list,
    dataset_path, dataset_snap, dataset_state, dataset_zip)
from core.services.dataset_record import (  # noqa: E402,F401
    _dataset_writer, _to_h264, dataset_record, dataset_record_stop)


_PAGE_RE = re.compile(r"_pg([a-z0-9]{1,16})_")


def snapshot_pages(systems_dir, slug, kind="snapshot"):
    """The pages Footage offers: [{slug, name}], one per named Snapshot node
    (photos) or Record Clip node (kind="record", clips), in the order the
    workflow has them."""
    seen, out = set(), []
    for o in get_outputs(systems_dir, slug):
        if o.get("type") == kind and o.get("page") and o["page"] not in seen:
            seen.add(o["page"])
            out.append({"slug": o["page"], "name": o.get("page_name") or o["page"]})
    return out


def list_snapshots(systems_dir, slug, limit=300):
    """The LOCAL app's saved snapshots (event/violation frames + workflow
    captures), newest first -- what the standalone Footage page lists. Rows:
    {id, mtime, size, camera, camera_name}."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    folder = os.path.join(_data_dir(pkg_dir, _merged(pkg_dir)), "snapshots")
    try:
        everything = os.listdir(folder)
    except OSError:
        return []
    names = [n for n in everything if n.endswith(".jpg")]
    # A photo taken before its node had a page carries none in its file name,
    # but a photo with a record (a .json beside it) knows which rule took it:
    # when exactly one named Snapshot node hangs off that rule, the photo is
    # that node's. Photos with neither stay on "All photos" -- nothing says
    # which node took them.
    records = {n[:-5] for n in everything if n.endswith(".json")}
    by_rule = {}
    for o in get_outputs(systems_dir, slug):
        if o.get("type") == "snapshot" and o.get("page") and o.get("function"):
            by_rule.setdefault(o["function"], set()).add(o["page"])
    by_rule = {fn: next(iter(pages)) for fn, pages in by_rule.items() if len(pages) == 1}
    cams = _capture_cameras(pkg_dir)
    rows = []
    for n in names:
        try:
            st = os.stat(os.path.join(folder, n))
        except OSError:
            continue
        stem = n[:-4]
        cid, cname = _tagged_camera(
            stem[4:] if stem.startswith("cap_") else stem, cams)
        pg = _PAGE_RE.search(stem)
        page = pg.group(1) if pg else ""
        if not page and by_rule and stem in records:
            try:
                with open(os.path.join(folder, stem + ".json"), "r", encoding="utf-8") as f:
                    page = by_rule.get(str(json.load(f).get("function") or ""), "")
            except (OSError, ValueError, AttributeError):
                page = ""
        rows.append({"id": stem, "mtime": int(st.st_mtime), "size": st.st_size,
                     "camera": cid, "camera_name": cname,
                     # the Snapshot node's page: in the file name (`..._pg<slug>_...`),
                     # or, for an older photo, from the rule its record names
                     "page": page,
                     # "snap" = a frame a Snapshot Detection Stream detected
                     # (capture.snap kind "snp"); "" for every other picture.
                     "kind": "snap" if "_snp_" in stem else ""})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:limit] if limit and limit > 0 else rows


def snapshot_thumb(systems_dir, slug, sid, width=360):
    """A cached small JPEG for the Footage grid, generated on first request
    beside the originals (.thumbs/). The grid used to pull every FULL frame
    (~100 KB each, dozens at once) through a Pi that is also running
    inference -- the thumbnails are ~10 KB and immutable. Falls back to the
    original when OpenCV is unavailable."""
    src = snapshot_file(systems_dir, slug, sid)
    if not src:
        return None
    folder = os.path.join(os.path.dirname(src), ".thumbs")
    dst = os.path.join(folder, f"{sid}.jpg")
    try:
        if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst
        import cv2                                             # noqa: PLC0415
        img = cv2.imread(src)
        if img is None:
            return src
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, max(1, int(h * width / w))),
                             interpolation=cv2.INTER_AREA)
        os.makedirs(folder, exist_ok=True)
        # imwrite picks its codec from the EXTENSION, so the temp name must
        # still end in .jpg -- ".tmp" alone has no writer and raises.
        tmp = dst + f".{os.getpid()}.tmp.jpg"
        if not cv2.imwrite(tmp, img, [int(cv2.IMWRITE_JPEG_QUALITY), 78]):
            return src
        os.replace(tmp, dst)
        return dst
    except Exception:                                          # noqa: BLE001
        return src


def snapshot_file(systems_dir, slug, sid):
    """Absolute path of one local snapshot, or None (basename check is the
    security boundary)."""
    if os.path.basename(sid) != sid:
        return None
    pkg_dir = _pkg_dir(systems_dir, slug)
    path = os.path.join(_data_dir(pkg_dir, _merged(pkg_dir)), "snapshots", sid + ".jpg")
    return path if os.path.isfile(path) else None


def snapshot_view(systems_dir, slug, sid):
    """What the photo window and the PDF notice show about one snapshot
    (services/snapshot_meta.py), or None when there is no such snapshot."""
    path = snapshot_file(systems_dir, slug, sid)
    if not path:
        return None
    pkg_dir = _pkg_dir(systems_dir, slug)
    _cid, cname = _tagged_camera(sid[4:] if sid.startswith("cap_") else sid,
                                 _capture_cameras(pkg_dir))
    return _snapshot_meta.view(sid, path, cname or "", slug)


def snapshot_crop_file(systems_dir, slug, sid, n):
    """The file of a snapshot's close-up number `n`, or None."""
    path = snapshot_file(systems_dir, slug, sid)
    return _snapshot_meta.crop_path(path, n) if path else None


def delete_snapshots(systems_dir, slug, ids):
    """Delete many snapshots at once. Returns how many went. Each id is
    resolved through snapshot_file, so the basename check still bounds every
    delete to this system's snapshot folder."""
    removed = 0
    for sid in ids or []:
        if delete_snapshot(systems_dir, slug, str(sid)):
            removed += 1
    return removed


def delete_snapshot(systems_dir, slug, sid):
    path = snapshot_file(systems_dir, slug, sid)
    if path:
        try:
            os.remove(path)
        except OSError:
            return False
        _snapshot_meta.remove_extras(path)      # its record and close-ups too
        return True
    return False


def scan_rtsp(port=554, timeout=0.35):
    """IPs on the local /24 subnet answering on the RTSP port -- candidate IP
    cameras for the picker. A plain TCP connect sweep (no ONVIF), ~2s total."""
    import concurrent.futures
    try:
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe_sock.connect(("8.8.8.8", 80))
        local_ip = probe_sock.getsockname()[0]
        probe_sock.close()
    except OSError:
        return []
    prefix = local_ip.rsplit(".", 1)[0]

    def probe(ip):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return ip
        except OSError:
            return None

    candidates = [f"{prefix}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        return sorted((ip for ip in pool.map(probe, candidates) if ip),
                      key=lambda s: int(s.rsplit(".", 1)[1]))


#: Concurrent detection apps allowed on one box. All of them share the single
#: Hailo NPU; past four, every stream's share of the chip is too small to be
#: useful, so refuse with a clear message instead of degrading everyone.
MAX_RUNNING_APPS = 4


def running_apps(systems_dir):
    """Slugs of every detection app running on this box right now, scanning
    both the repo systems dir and the generated store."""
    roots = {str(systems_dir), str(config_mod.generated_systems_dir())}
    out = []
    for root in roots:
        try:
            slugs = sorted(os.listdir(root))
        except OSError:
            continue
        for other in slugs:
            if other in out:
                continue
            try:
                if status(root, other).get("running"):
                    out.append(other)
            except Exception:           # noqa: BLE001 -- not a runnable package
                continue
    return out


#: Architectures whose systems SHARE the accelerator by default, through the
#: HailoRT service. Mirrors systems/*/app/hailo.py `_default_multi_process`,
#: and must keep mirroring it: this decides what the page refuses, that
#: decides what the app does, and they cannot be allowed to disagree.
SHARING_ARCHS = ("hailo8", "hailo8l")


def uses_chip(systems_dir, slug):
    """Does this system's detector run on the Hailo accelerator?"""
    try:
        cfg = _merged(_pkg_dir(systems_dir, slug))
    except Exception:                                          # noqa: BLE001
        return False
    return str(((cfg.get("detector") or {}).get("kind") or "")).strip() in (
        "hailo", "hailo-multi", "gstreamer")


def shares_chip(systems_dir, slug, arch=None):
    """Whether this system opens the accelerator in a way that lets ANOTHER
    system use it at the same time.

    Explicit beats default, both ways -- the same rule the app applies, so a
    `multi_process = false` written on purpose is honoured here too.
    """
    try:
        cfg = _merged(_pkg_dir(systems_dir, slug))
    except Exception:                                          # noqa: BLE001
        return False
    asked = ((cfg.get("detector") or {}).get("hailo") or {}).get("multi_process")
    if asked is not None:
        return bool(asked)
    if arch is None:
        try:
            from core.services import builtin_models           # noqa: PLC0415
            arch = builtin_models.detect_arch()
        except Exception:                                      # noqa: BLE001
            arch = None
    return str(arch or "") in SHARING_ARCHS


def chip_holder(systems_dir, slug):
    """The other system already using the accelerator exclusively, or "".

    A Hailo-10H opens EXCLUSIVELY (multi_process is off there by design), and
    a second exclusive open does not fail -- it takes the chip, and the app
    that had it sees "VDevice released while the CIM is in use". That app
    falls back, retries, and takes it back. Measured on a live box: two
    systems see-sawing for as long as both were running, each reporting
    HAILO_COMMUNICATION_CLOSED, each recovering, neither detecting reliably,
    and the page for whichever had just lost it saying the AI was not running.

    MAX_RUNNING_APPS (4) does not cover this: it is about the chip's THROUGHPUT
    being split, which assumes the systems can share it at all.
    """
    if not uses_chip(systems_dir, slug):
        return ""
    try:
        from core.services import builtin_models               # noqa: PLC0415
        arch = builtin_models.detect_arch()
    except Exception:                                          # noqa: BLE001
        arch = None
    if shares_chip(systems_dir, slug, arch):
        return ""
    for other in running_apps(systems_dir):
        if other == slug:
            continue
        if uses_chip(systems_dir, other):
            return other
    return ""



def _app_locked(fn):
    """Run `fn(systems_dir, slug)` holding the app lock (app_lock): the hub
    and the master both start and stop apps, and without it a Start could
    re-adopt an app spawned a moment before. Re-entrant, so a
    Start detection that replaces an old logic-mode app holds it once."""
    import functools                                           # noqa: PLC0415

    @functools.wraps(fn)
    def locked(systems_dir, slug, *args, **kwargs):
        from core.services import app_lock                     # noqa: PLC0415
        with app_lock.held(slug):
            return fn(systems_dir, slug, *args, **kwargs)
    return locked

@_app_locked
def start(systems_dir, slug, require_camera=True):
    """Start AI detection: the camera(s) and the detector, IN the app.

    One app per system (app/run.py ai_config): this switches its AI on and
    makes sure it is running. A running app opens the camera and the model on
    its next config poll with every other rule left as it was; a stopped one
    is started. The checks (a camera, the running-apps cap, the camera
    budget) run first, because this is where the camera and the chip are
    spent. An app from before the switch is started the old way.

    `require_camera`: a person's Start (the page, the dashboard, System
    Control) is REFUSED when not one of the system's cameras is on the box
    -- a USB node that opens, a network host that answers -- and told
    which camera, by its name. The alternative was a Start that "succeeded"
    into a page saying Live beside a toast saying the camera was not found.
    Off for the Starts nobody is watching (a boot autostart, a reset's
    restart, Power on): the camera often comes up after the box, so those
    start into the app's own wait (camwatch) and it opens the camera when
    it is back."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    rt = _app_runtime(pkg_dir)
    if rt is None:
        raise ValueError("this system has no detection app (app/runtime.py)")
    # The previous run is still releasing the camera and the chip: a Start
    # now (this tab, another tab, the dashboard) would race the teardown.
    # Refused with its own error, a 409 app-wide (core/routes/logs.py).
    if status(systems_dir, slug).get("stopping"):
        raise power_state.StoppingError(power_state.MESSAGE)
    # A system nobody has pointed at hardware yet must not "run". It used to
    # start against a synthetic placeholder, which looked exactly like a
    # working system -- counts ticking, zones armed -- while watching a
    # generated picture. Refusing here is the only place that covers every
    # caller (the page's Start, the hub power route, autostart at boot).
    mine = live_cameras(systems_dir, slug)
    if not mine:
        raise ValueError(
            "no camera is set up for this system - choose a camera on the "
            "Camera Setup page before starting detection")
    # NOT enforced as a hard block: the product limit is MAX_RUNNING_APPS
    # systems sharing MAX_CAMERAS cameras, whatever the chip -- confirmed by
    # the operator, twice, after this refusal shipped for one day (af34895)
    # on the strength of a live contention measurement (two systems
    # see-sawing an exclusive Hailo-10H, each reporting
    # HAILO_COMMUNICATION_CLOSED while the other held it). chip_holder()
    # stays as a DIAGNOSTIC a page can surface as a heads-up, not a gate.
    live = [s for s in running_apps(systems_dir) if s != slug]
    if len(live) >= MAX_RUNNING_APPS:
        raise ValueError(
            f"{len(live)} detection apps are already running ({', '.join(live)}) - "
            f"the maximum is {MAX_RUNNING_APPS} at once. Stop one first.")
    # The BOX's camera budget, checked at the moment it is actually spent.
    #
    # set_cameras() checks this too, but against what was running WHEN THE
    # CAMERAS WERE SAVED. Three cameras configured on an idle box is a legal
    # configuration that becomes an illegal one as soon as another system
    # with two of its own is started -- and nothing re-checked at that point,
    # so a four-camera box happily ran five feeds and then said "5 of 4" on
    # its own capacity meter. The budget has to be enforced where it is
    # spent, which is here.
    elsewhere = sources_elsewhere(systems_dir, slug)
    if len(mine) + len(elsewhere) > MAX_CAMERAS:
        others = ", ".join(live) or "another system"
        raise ValueError(
            f"this box runs at most {MAX_CAMERAS} cameras at once. This "
            f"system needs {len(mine)}, and {len(elsewhere)} "
            f"{'is' if len(elsewhere) == 1 else 'are'} already running on "
            f"{others}. Stop one of those, or switch one of this system's "
            "cameras off (its settings are kept), then start again.")
    if require_camera:
        from core.services import camera_notice                # noqa: PLC0415
        gone = camera_notice.missing(pkg_dir, mine)
        if gone:
            raise ValueError(camera_notice.refusal(gone))
    # Starting detection is the operator bringing this system UP, so it ends
    # a Hub Power off as surely as Power on does -- but only once the checks
    # above have passed. Cleared BEFORE them, a Start refused for "no camera"
    # still ended the Power off, and the route's error path then had the
    # supervisor bring the workflow up for a system the operator had switched
    # off. (The boot autostart never gets here for a powered-off system: it
    # checks the flag first.)
    try:
        if powered_off(systems_dir, slug):
            set_powered_off(systems_dir, slug, False)
    except Exception:                                          # noqa: BLE001
        pass
    data_dir = _data_dir(pkg_dir, _merged(pkg_dir))
    try:
        now_st = rt.status(data_dir, pkg_dir)
    except TypeError:
        now_st = rt.status(data_dir)
    if not getattr(rt, "UNIFIED", False):
        # This system's own copy of the app predates the switch: detection
        # IS the app, so start it (it refuses when already running).
        ok, message = rt.start(pkg_dir, data_dir)
        return {"ok": bool(ok), "message": message}
    set_ai(systems_dir, slug, True)
    if now_st.get("running"):
        if now_st.get("mode") == "unified":
            return {"ok": True, "message": "AI detection switched on"}
        if now_st.get("mode") != "logic":
            # A detection app from before the switch: it is detecting already
            # and is replaced at its next stop.
            return {"ok": False, "message": "already running"}
        # An old logic-mode app (a box updated from v1.0.12): it cannot
        # follow the switch, so it is replaced, once, by the unified app.
        rt.stop(data_dir, pkg_dir=pkg_dir)
    ok, message = rt.start(pkg_dir, data_dir, mode="unified")
    return {"ok": bool(ok), "message": message}


def set_logic(systems_dir, slug, frames_free):
    """Record which compiled functions and outputs run WITHOUT detection, as
    [logic] in config.local.toml -- what the app runs with its AI off
    (app/run.py ai_config). None (a compile that said nothing) removes the
    table, and the app falls back to its own type list."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    with edit_local(pkg_dir) as local:
        if not isinstance(frames_free, dict):
            local.pop("logic", None)
            return
        clean = lambda xs: [str(x)[:60] for x in (xs or [])            # noqa: E731
                            if isinstance(x, str) and x.strip()][:400]
        local["logic"] = {"functions": clean(frames_free.get("functions")),
                          "outputs": clean(frames_free.get("outputs")),
                          # Which rules decided it (workflow.LOGIC_VERSION): an
                          # older list is recompiled at the next master start.
                          "version": int(frames_free.get("version") or 0)}




def live_cameras(systems_dir, slug):
    """The cameras this system would actually open: a real source, enabled.
    "synthetic" counts as unset -- a config written before the placeholder
    was withdrawn must not keep a system running on a generated picture."""
    return [c for c in cameras(systems_dir, slug)
            if str(c.get("source") or "").strip() not in ("", "synthetic")
            and c.get("enabled", True) is not False]


def powered_off(systems_dir, slug):
    """Did the operator POWER THIS SYSTEM OFF on the Hub (and not on again)?

    Remembered in the system's own config.local.toml [power], because a
    master under systemd (Restart=always) comes back by itself seconds after
    it is killed -- and a master that came back must not bring the workflow
    with it. Everything that starts an app by itself reads this: the logic
    runtime and its supervisor, and the hub's autostart at boot."""
    try:
        local = _read_toml(_local_path(_pkg_dir(systems_dir, slug)))
    except Exception:                                          # noqa: BLE001
        return False
    return bool((local.get("power") or {}).get("off"))


def set_powered_off(systems_dir, slug, off):
    """Record the Hub's Power off (True) / Power on (False) for this system."""
    with edit_local(_pkg_dir(systems_dir, slug)) as local:
        if off:
            local["power"] = {"off": True, "at": int(time.time())}
        else:
            local.pop("power", None)


def unified_supported(systems_dir, slug):
    """Does this system's OWN copy of the app have the AI switch?

    Each system runs the app under the store, and an older copy started with
    the AI "off" would ignore [ai] and open the camera and the model -- so
    nothing starts it by itself (app_supervisor) until it is updated."""
    rt = _app_runtime(_pkg_dir(systems_dir, slug))
    return bool(rt is not None and getattr(rt, "UNIFIED", False))


@_app_locked
def ensure_app(systems_dir, slug):
    """Make sure the unified app is running, AI OFF if it has to be started.

    What the supervisor runs (app_supervisor): the workflow is up whenever
    the system is online. Started with the AI off -- only Start detection
    (the operator, or the boot autostart) turns it on, so a box that reboots
    or an app that crashes never brings detection back by itself. An old
    logic-mode app is replaced, once. A running app is left as it is.
    Returns {ok, message, word}: running | replaced | started | failed."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    rt = _app_runtime(pkg_dir)
    if rt is None or not getattr(rt, "UNIFIED", False):
        raise ValueError("this system's app is too old to run its workflow "
                         "without detection - update the system")
    data_dir = _data_dir(pkg_dir, _merged(pkg_dir))
    st = rt.status(data_dir, pkg_dir)
    word = "started"
    if st.get("running"):
        if st.get("mode") != "logic":
            return {"ok": True, "message": "already running", "word": "running"}
        rt.stop(data_dir, pkg_dir=pkg_dir)
        word = "replaced"
    set_ai(systems_dir, slug, False)
    ok, message = rt.start(pkg_dir, data_dir, mode="unified")
    return {"ok": bool(ok), "message": message,
            "word": word if ok else "failed"}


@_app_locked
def stop(systems_dir, slug):
    """Stop AI detection: camera and chip released, the workflow KEEPS
    running (app/run.py ai_config). Stopping the whole app -- Power off,
    delete, a counters reset -- is stop_app().

    An app from before the switch cannot release them in place: it is
    stopped, and the unified app started in its place with the AI off, unless
    the system is powered off."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    rt = _app_runtime(pkg_dir)
    if rt is None:
        raise ValueError("this system has no detection app (app/runtime.py)")
    if not getattr(rt, "UNIFIED", False):
        return stop_app(systems_dir, slug)
    set_ai(systems_dir, slug, False)
    data_dir = _data_dir(pkg_dir, _merged(pkg_dir))
    st = rt.status(data_dir, pkg_dir)
    if st.get("running") and st.get("mode") == "unified":
        return {"ok": True, "message": "AI detection switched off - the rest "
                                       "of the workflow keeps running"}
    res = stop_app(systems_dir, slug) if st.get("running") \
        else {"ok": False, "message": "not running"}
    if not powered_off(systems_dir, slug):
        try:
            ensure_app(systems_dir, slug)
        except ValueError:
            pass
    return res


@_app_locked
def stop_app(systems_dir, slug):
    """Stop the system's app process -- the whole workflow, not only the AI.

    For Power off, delete and a counters reset. The AI switch is left as it
    is; whatever starts the app next decides it (ensure_app: off)."""
    pkg_dir = _pkg_dir(systems_dir, slug)
    rt = _app_runtime(pkg_dir)
    if rt is None:
        raise ValueError("this system has no detection app (app/runtime.py)")
    # pkg_dir lets stop() reach a stray the pid file lost track of -- an app
    # whose record was overwritten by a twin that died. Older app code (a
    # created system not yet refreshed) has the two-argument signature.
    try:
        ok, message = rt.stop(_data_dir(pkg_dir, _merged(pkg_dir)),
                              pkg_dir=pkg_dir)
    except TypeError:
        ok, message = rt.stop(_data_dir(pkg_dir, _merged(pkg_dir)))
    return {"ok": bool(ok), "message": message}
