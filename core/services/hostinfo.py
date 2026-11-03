# -*- coding: utf-8 -*-
"""
hostinfo.py -- what the box IS and what it is doing, for the front panel.

This is the Python of the factory Node-RED "Update Status" group: the same
facts, gathered on the same two cadences (a 2-second poll for what moves, one
read at start for what cannot), published for the OLED pages and the UI.

The commands are gone, not translated. Node-RED reached for `top | grep |
awk` because a flow has no other way to compute; a process that can open
/proc reads /proc. Half a dozen shell pipelines per screen refresh on a box
that is also decoding video is a cost with nothing to show for it -- and
`vcgencmd` and `hailortcli`, the two facts genuinely only a tool knows, stay
as subprocesses and are read ONCE or slowly.

Every reader returns a fallback rather than raising. A panel that cannot say
the temperature should say the IP addresses it does know, not go blank.
"""

import hashlib
import os
import re
import socket
import struct
import subprocess
import threading
import time

try:                                    # Linux only; the ioctl path needs it
    import fcntl
except ImportError:
    fcntl = None

#: The static facts cost a fork each and never change while the box is up.
_STATIC_TTL = None          # read once
#: The moving ones drive a screen a person is watching.
_LIVE_TTL = 2.0

_lock = threading.Lock()
_static = {}
_live = {}
_live_at = 0.0


def _run(argv, timeout=8):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return default


# -- the moving facts ---------------------------------------------------------

#: SIOCGIFADDR -- "what IPv4 address does this interface have", asked of the
#: kernel directly. There is no sysfs file for an address, but there is an
#: ioctl, and it costs microseconds where `ip addr` costs a fork.
_SIOCGIFADDR = 0x8915


def ip_of(iface):
    """The interface's first IPv4 address, or "".

    NO SUBPROCESS. This is called for three interfaces every refresh, on a
    box that is also decoding video: three forks plus `vcgencmd` measured
    58ms per refresh, which at one refresh every two seconds is three percent
    of a core spent asking questions the kernel answers in a syscall.

    Falls back to `ip` where the ioctl is unavailable (a non-Linux dev box),
    because a developer's laptop rendering the panel preview should still
    show a real address rather than nothing.
    """
    iface = str(iface)
    if fcntl is not None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            packed = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR,
                                 struct.pack("256s", iface[:15].encode()))
            return socket.inet_ntoa(packed[20:24])
        except OSError:
            # No such interface, or it has no address yet. Both are "".
            return ""
        finally:
            if sock is not None:
                sock.close()
    out = _run(["ip", "-4", "-o", "addr", "show", "dev", iface])
    match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", out)
    return match.group(1) if match else ""


def cpu_load():
    """1-minute load average, two decimals."""
    try:
        return "%.2f" % float(_read("/proc/loadavg", "0").split()[0])
    except (ValueError, IndexError):
        return "0.00"


def cpu_temp():
    """CPU temperature as "56.3'C", or "" if nothing will say.

    The THERMAL ZONE first, `vcgencmd` only if it is missing. They report the
    same sensor to the same tenth of a degree -- one is a file read, the other
    is a fork, and this is read every couple of seconds forever. The firmware
    tool stays as the fallback for an image where the zone is not exposed.
    """
    raw = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    try:
        return "%.1f'C" % (int(raw) / 1000.0)
    except ValueError:
        pass
    out = _run(["vcgencmd", "measure_temp"])
    match = re.search(r"=\s*([\d.]+)", out)
    return "%s'C" % match.group(1) if match else ""


def _meminfo():
    values = {}
    for line in _read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                values[parts[0][:-1]] = int(parts[1])       # kB
            except ValueError:
                pass
    return values


def ram_usage():
    """"1.7/8.1GB 21%" -- the same sentence `free -m | awk` produced.

    Used is total minus AVAILABLE, which is what `free` itself reports in its
    used column on any kernel this side of 2014: counting cache as used makes
    a healthy Linux box look permanently full.
    """
    info = _meminfo()
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    if not total:
        return ""
    used = max(0, total - available)
    return "%.1f/%.1fGB %d%%" % (used / 1e6, total / 1e6,
                                 round(used * 100.0 / total))


def disk_usage(path="/"):
    """"21/29GB 77%" for the root filesystem."""
    try:
        # AttributeError as well as OSError: statvfs does not exist at all on
        # Windows, and this module is imported on a developer's laptop every
        # time the panel's pages are rendered for the preview.
        stat = os.statvfs(path)
    except (OSError, AttributeError):
        return ""
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bfree * stat.f_frsize
    # `df` reports the percentage against what a non-root user can actually
    # have, which is why its number is higher than used/total. Same maths.
    avail = stat.f_bavail * stat.f_frsize
    used = total - free
    denominator = used + avail
    if not total:
        return ""
    percent = round(used * 100.0 / denominator) if denominator else 0
    return "%.0f/%.0fGB %d%%" % (used / 1e9, total / 1e9, percent)


# -- the fixed facts ----------------------------------------------------------

def serial_number():
    match = re.search(r"^Serial\s*:\s*(\S+)", _read("/proc/cpuinfo"),
                      re.MULTILINE)
    return match.group(1) if match else ""


def codename():
    """Debian release codename, capitalised: "Trixie"."""
    out = _run(["lsb_release", "-c"])
    name = out.split(":", 1)[1].strip() if ":" in out else ""
    if not name:
        match = re.search(r'VERSION_CODENAME=("?)(\w+)\1',
                          _read("/etc/os-release"))
        name = match.group(2) if match else ""
    return name[:1].upper() + name[1:] if name else ""


def os_bits():
    out = _run(["getconf", "LONG_BIT"]).strip()
    return out or ("64" if sys_is_64() else "32")


def sys_is_64():
    import platform                                          # noqa: PLC0415
    return platform.machine().endswith("64")


def _bucket(value, steps):
    for limit, label in steps:
        if value < limit:
            return label
    return steps[-1][1]


def total_ram_gb():
    """RAM rounded to the size on the label: 1, 2, 4, 8, 16, 32.

    A CM5 with 8GB fitted reports about 7.9 usable, and "R7" is not a model
    anybody can look up. The factory flow rounds for the same reason.
    """
    total = _meminfo().get("MemTotal", 0) / 1e6
    return _bucket(total, ((1.5, "1"), (3, "2"), (6, "4"), (12, "8"),
                           (24, "16"), (float("inf"), "32")))


def total_disk_gb():
    """eMMC/NVMe size rounded to the size on the label."""
    try:
        stat = os.statvfs("/")
        size = stat.f_blocks * stat.f_frsize / 1e9 * 1.1
    except (OSError, AttributeError):
        return "0"
    return _bucket(size, ((4, "0"), (12, "8"), (24, "16"), (48, "32"),
                          (96, "64"), (192, "128"), (384, "256"),
                          (768, "512"), (1536, "1024"), (float("inf"), "2048")))


def is_wireless():
    """"W" when the box has Wi-Fi, "" when it has not.

    One letter because it is a MODEL SUFFIX, not a status: it prefixes the
    "WR8E64" line on the panel's first page, exactly as the factory flow
    composed it.
    """
    return "W" if os.path.isdir("/sys/class/net/wlan0") else ""


def ai_hat():
    """The accelerator's architecture, as its own tooling names it.

    The factory flow rewrites HAILO10 to "AI13T" and then has a second,
    unreachable branch rewriting the same string to "AI26T" -- a copy-paste
    slip, and the reason a 10H currently prints its raw architecture on the
    panel. Only the two mappings that are actually true are applied here;
    anything else passes through verbatim, which is both honest and exactly
    what the board shows today.
    """
    out = _run(["hailortcli", "fw-control", "identify"], timeout=15)
    match = re.search(r"Device Architecture:\s*(\S+)", out)
    if not match:
        return ""
    arch = match.group(1).upper()
    if arch.startswith("HAILO8L"):
        return "AI13T"
    if arch.startswith("HAILO8"):
        return "AI26T"
    return arch


def revision():
    """The platform's own version, not the carrier board's.

    The flow this replaces hard-coded "v1.0.2" -- the revision of the flow
    itself, which stopped meaning anything the moment the flow was replaced.
    What an operator reading the panel needs is the software they would be
    asked for on a support call.
    """
    try:
        from core import version as version_mod                # noqa: PLC0415
        return "v" + str(version_mod.VERSION)
    except Exception:                                          # noqa: BLE001
        return ""


# -- the published snapshot ---------------------------------------------------

def _refresh_static():
    return {
        "serial": serial_number(),
        "codename": codename(),
        "os_bits": os_bits(),
        "total_ram": total_ram_gb(),
        "total_disk": total_disk_gb(),
        "wireless": is_wireless(),
        "ai_hat": ai_hat(),
        "revision": revision(),
    }


def _refresh_live():
    return {
        "ip_eth0": ip_of("eth0"),
        "ip_eth1": ip_of("eth1"),
        "ip_wlan0": ip_of("wlan0"),
        "cpu_load": cpu_load(),
        "cpu_temp": cpu_temp(),
        "ram": ram_usage(),
        "disk": disk_usage(),
    }


def snapshot(force=False):
    """Everything the panel draws, as one dict.

    The static half is read once per process; the moving half at most every
    two seconds however often this is called, so a browser polling the
    preview cannot make the box work harder than the panel already does.
    """
    global _live, _live_at
    with _lock:
        if not _static or force:
            _static.update(_refresh_static())
        now = time.monotonic()
        if force or not _live or (now - _live_at) >= _LIVE_TTL:
            _live = _refresh_live()
            _live_at = now
        out = dict(_static)
        out.update(_live)
        return out


def default_hostname():
    """"iriv-<last 8 of serial>": all boxes "iriv" made iriv.local a race."""
    serial = (_static.get("serial") or serial_number()).lower()
    return "iriv-%s" % serial[-8:] if serial else "iriv"


def ap_name():
    """The access point this box broadcasts: "iriv-<serial>".

    Named from the serial so every unit is unique on a bench with twenty of
    them, and so the name on the OLED is the name in the Wi-Fi list.
    """
    serial = _static.get("serial") or serial_number()
    return "iriv-%s" % serial if serial else "iriv"


def box_id():
    """A short tag that says "this box" and nothing else: 16 hex characters of
    a hash of the serial (the machine id, then the hostname, off a Pi).

    What the Wi-Fi switch's page compares against when it probes addresses
    the box may have moved to (GET /api/health/box, open): a DHCP lease the
    box used to hold may belong to another device now, and another IRIV box
    answers the same endpoint. Hashed, so that endpoint hands out a match key
    rather than the serial itself -- though the AP name already broadcasts it.
    """
    seed = (_static.get("serial") or serial_number()
            or _read("/etc/machine-id").strip() or socket.gethostname())
    return hashlib.sha256(("iriv-box:" + seed).encode("utf-8")).hexdigest()[:16]
