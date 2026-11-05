# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
host_health.py -- the health of THIS box: the machine running the dashboards and
detection, and its Hailo NPU.

All systems on one board share the same CPU, memory and accelerator, so their
resource usage is a property of the BOX, not of any one system. This reports it
once: psutil for CPU/memory/disk/temperature, and hailortcli for the NPU's
architecture, temperature and (where the runtime exposes it) power. Everything
degrades -- a host without psutil or without a Hailo simply reports None for the
parts it cannot measure, never an error.
"""

import json
import os
import re
import socket
import subprocess
import tempfile
import time


def _run(argv, timeout=5):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_temp():
    """CPU temperature in °C from psutil, falling back to the thermal-zone sysfs
    (a Pi exposes it there even when psutil has no sensor for it)."""
    try:
        import psutil                                       # noqa: PLC0415
        temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        for entries in temps.values():
            if entries and entries[0].current:
                return float(entries[0].current)
    except Exception:                                       # noqa: BLE001
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def _active_chip_apps():
    """How many detection apps report using the accelerator right now, from the
    apps' shared heartbeat registry (systems/*/app/camera.py). 0 if none/absent."""
    try:
        path = os.path.join(tempfile.gettempdir(), "cytron_cameras.json")
        with open(path, "r", encoding="utf-8") as f:
            apps = (json.load(f) or {}).get("_apps") or {}
    except (OSError, ValueError):
        return 0
    now = time.time()
    count = 0
    for pid, e in apps.items():
        if not e.get("chip") or (now - e.get("ts", 0)) > 20:
            continue
        try:
            os.kill(int(pid), 0)
            count += 1
        except (OSError, ValueError):
            continue
    return count


def _npu_temp_py():
    """Chip temperature via hailo_platform's control API. {} when unavailable
    (no bindings on this host, no device, or a transient control failure)."""
    try:
        from hailo_platform import Device                   # noqa: PLC0415
        ids = Device.scan()
        if not ids:
            return {}
        with Device(ids[0]) as dev:
            info = dev.control.get_chip_temperature()
            for attr in ("ts0_temperature", "ts1_temperature", "temperature"):
                v = getattr(info, attr, None)
                if v:
                    return {"temp_c": float(v)}
    except Exception:                                       # noqa: BLE001
        pass
    return {}


#: The identify answer, cached. `hailortcli fw-control identify` costs ~50ms of
#: SUBPROCESS on this Pi, and every field it returns -- architecture, serial,
#: firmware -- is fixed for as long as the box is up. A miss is cached too, but
#: for a shorter time, so a Hailo that appears later is still found.
_ident_cache = None                                     # (when, dict)
IDENT_TTL_S = 300.0
IDENT_MISS_TTL_S = 60.0


def _npu_identify():
    """{present, arch, serial, fw} from hailortcli, cached. {} with no Hailo."""
    global _ident_cache                                 # noqa: PLW0603
    now = time.time()
    if _ident_cache is not None:
        when, cached = _ident_cache
        ttl = IDENT_TTL_S if cached else IDENT_MISS_TTL_S
        if now - when < ttl:
            return dict(cached)
    npu = {}
    ident = _run(["hailortcli", "fw-control", "identify"])
    if ident and "hailo" in ident.lower():
        npu["present"] = True
        m = re.search(r"Architecture:\s*(\S+)", ident, re.I)
        if m:
            npu["arch"] = m.group(1)
        m = re.search(r"Serial Number:\s*(\S+)", ident, re.I)
        if m:
            npu["serial"] = m.group(1)
        m = re.search(r"Firmware Version:\s*(\S+)", ident, re.I)
        if m:
            npu["fw"] = m.group(1)
    _ident_cache = (now, dict(npu))
    return npu


def _npu():
    """Hailo accelerator health from hailortcli: present, arch, firmware,
    temperature, power, and how many detection apps are running models on it.
    Empty dict when no Hailo/hailortcli is on the box."""
    npu = _npu_identify()
    # Temperature: HailoRT 5.x has NO `measure-temp` CLI subcommand -- read it
    # through the Python control API instead (the same hailo_platform the apps
    # use). A control open coexists with a running pipeline; failure is a quiet
    # None, never an error.
    npu.update(_npu_temp_py())
    # Power: the 10H module here answers HAILO_OPEN_FILE_FAILURE -- no readable
    # power sensor. Try once, and translate that answer into an honest note.
    power = _run(["hailortcli", "measure-power"])
    mp = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*m?W\b", power or "")
    if mp:
        value = float(mp.group(1))
        if re.search(r"\bmW\b", power or ""):
            value /= 1000.0
        npu["power_w"] = value
    elif "OPEN_FILE_FAILURE" in (power or ""):
        npu["power_note"] = "power sensor not available on this module"
    elif power and "error" in power.lower():
        npu["power_note"] = power.strip().splitlines()[0][:120]
    apps = _active_chip_apps()
    if npu.get("present") or apps:
        npu["active_models"] = apps
        # Health verdict the page can badge: ok / hot / idle / absent.
        if not npu.get("present"):
            npu["status"] = "absent"
        elif npu.get("temp_c") is not None and npu["temp_c"] >= 85:
            npu["status"] = "hot"
        elif apps:
            npu["status"] = "running"
        else:
            npu["status"] = "idle"
    return npu


def root_disk_label(device, sys_root="/sys", dev_root="/dev"):
    """What the disk / lives on IS, from the device itself: eMMC, an SD card,
    or a System disk.

    The CM5 boards boot from eMMC, but the same image runs on a Lite module
    from an SD card, and on an NVMe or USB disk -- a label hard-coded either
    way is wrong on some box. An MMC block device says which it is: an eMMC
    has hardware boot partitions (mmcblkNboot0/boot1) and an RPMB, and sysfs
    names its card type (MMC for eMMC, SD for an SD card). Anything that is
    not an mmcblk -- or cannot be read -- is a plain "System disk".
    (The one place the product names an SD card -- smoke_betawording.)"""
    try:
        dev = os.path.realpath(str(device or ""))
    except OSError:
        dev = str(device or "")
    name = os.path.basename(dev)
    m = re.match(r"^(mmcblk\d+)(?:p\d+)?$", name)
    if not m:
        return "System disk"
    disk = m.group(1)
    try:
        with open(os.path.join(sys_root, "block", disk, "device", "type"),
                  encoding="utf-8") as f:
            kind = f.read().strip().upper()
    except OSError:
        kind = ""
    if kind == "MMC" or any(os.path.exists(os.path.join(dev_root, disk + s_))
                            for s_ in ("boot0", "boot1", "rpmb")):
        return "eMMC"
    if kind == "SD":
        return "SD card"                                       # disk-label-ok
    return "System disk"


def _storages():
    """Every user-relevant filesystem: the root (the eMMC, or whatever
    root_disk_label finds) plus anything mounted
    under /mnt or /media (the NVMe). One row per mount:
    {mount, label, total_gb, percent}."""
    rows = []
    try:
        import psutil                                       # noqa: PLC0415
        seen = set()
        for part in psutil.disk_partitions(all=False):
            mp = part.mountpoint
            if os.name != "nt" and mp != "/" and \
                    not mp.startswith(("/mnt", "/media")):
                continue
            if mp in seen:
                continue
            seen.add(mp)
            try:
                usage = psutil.disk_usage(mp)
            except OSError:
                continue
            rows.append({
                "mount": mp,
                "label": (root_disk_label(part.device) if mp == "/"
                          else (os.path.basename(mp.rstrip("/")) or mp).upper()),
                "total_gb": round(usage.total / 1e9, 1),
                "used_gb": round(usage.used / 1e9, 1),
                "percent": usage.percent,
            })
    except Exception:                                       # noqa: BLE001
        pass
    return rows


def host_health():
    """One snapshot of this box's health + its NPU. Safe on any host."""
    out = {
        "hostname": socket.gethostname(),
        "cpu_percent": None, "mem_percent": None, "disk_percent": None,
        "cpu_temp_c": _cpu_temp(),
        "cpu_count": None, "load1": None,
        "npu": {},
    }
    try:
        import psutil                                       # noqa: PLC0415
        # The footer's sampler: CPU% since the previous caller, no wait.
        # Only the very first call has no previous sample, and pays the
        # blocking 0.25 s read once rather than report a made-up 0 %.
        cpu = _cpu_percent_since_last()
        out["cpu_percent"] = cpu if cpu is not None \
            else psutil.cpu_percent(interval=0.25)
        out["mem_percent"] = psutil.virtual_memory().percent
        out["disk_percent"] = psutil.disk_usage("/").percent
        out["cpu_count"] = psutil.cpu_count()
        try:
            out["load1"] = round(psutil.getloadavg()[0], 2)
        except (AttributeError, OSError):
            pass
    except Exception:                                       # noqa: BLE001
        pass
    out["storages"] = _storages()
    out["npu"] = _npu()
    return out


# -- the hardware footer ------------------------------------------------------
# host_health() above is a PAGE's snapshot: it is read once, and it can afford
# to block a quarter of a second and shell out twice for it. The footer is a
# different thing entirely -- it sits under Detection, OCR, Zones and Workflow
# and samples every couple of seconds while the box is doing the heaviest work
# it ever does. Measured on this Pi (min of 5):
#
#   psutil.cpu_percent(interval=0.25)  250.29 ms   <- blocks; 12% of a core at 2s
#   psutil.cpu_times()                   0.041 ms  <- the same number, no wait
#   psutil.virtual_memory()              0.035 ms
#   psutil.sensors_temperatures()       10.10 ms   <- walks every hwmon node
#   /sys/class/thermal/thermal_zone0     0.017 ms  <- the same °C, 600x cheaper
#   hailortcli fw-control identify      49.94 ms   <- subprocess; fixed answer
#   hailo_platform chip temperature     52.21 ms   <- opens the device
#   the camera heartbeat registry        0.021 ms
#
# So the footer's per-sample budget is the four cheap rows (~0.11 ms), and the
# two 50 ms readings are cached: the identify for IDENT_TTL_S (nothing in it can
# change while the box is up) and the chip temperature for NPU_TEMP_TTL_S, which
# is far longer than the poll but far shorter than a thermal event.

#: The previous CPU sample: (busy_seconds, total_seconds). CPU% is a RATE, so it
#: needs two readings -- the blocking form takes the second one itself, by
#: sleeping. Keeping the previous sample here means the second reading is simply
#: the next poll, and the wait costs nothing because it already happened.
#:
#: One consequence worth knowing: the window is "since the last CALLER", not
#: "since the last caller in this tab". Two browsers polling this master halve
#: each other's window. The answer stays honest -- it is still real busy time
#: over real elapsed time -- it just averages over less of it.
_cpu_prev = None
_cpu_last = None


def _cpu_percent_since_last():
    """CPU% over the window since the PREVIOUS call. None on the first call --
    one reading cannot be a rate, and reporting that as 0% would be a lie the
    footer has no way to distinguish from a genuinely idle box."""
    global _cpu_prev, _cpu_last                         # noqa: PLW0603
    try:
        import psutil                                   # noqa: PLC0415
        times = psutil.cpu_times()._asdict()
    except Exception:                                   # noqa: BLE001
        return None
    total = float(sum(times.values()))
    idle = float(times.get("idle", 0.0)) + float(times.get("iowait", 0.0))
    prev, _cpu_prev = _cpu_prev, (total - idle, total)
    if prev is None:
        return None
    d_busy, d_total = (total - idle) - prev[0], total - prev[1]
    if d_total <= 0:
        return _cpu_last        # two samples inside one clock tick
    _cpu_last = round(max(0.0, min(100.0, d_busy / d_total * 100.0)), 1)
    return _cpu_last


#: The psutil temperature path, cached: it is the fallback for hosts with no
#: thermal_zone0 (a Windows dev box), and at 10 ms it must not be paid per poll.
_temp_cache = None                                      # (when, °C or None)
TEMP_FALLBACK_TTL_S = 30.0


def _cpu_temp_fast():
    """CPU °C for a per-second poll: the thermal zone directly (0.017 ms on this
    Pi) rather than psutil's sensor walk (10 ms), which is only consulted -- and
    then cached -- on a host that has no thermal zone to read."""
    global _temp_cache                                  # noqa: PLW0603
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        pass
    now = time.time()
    if _temp_cache is not None and now - _temp_cache[0] < TEMP_FALLBACK_TTL_S:
        return _temp_cache[1]
    value = None
    try:
        import psutil                                   # noqa: PLC0415
        temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        for entries in temps.values():
            if entries and entries[0].current:
                value = float(entries[0].current)
                break
    except Exception:                                   # noqa: BLE001
        value = None
    _temp_cache = (now, value)
    return value


#: The chip temperature, cached. Opening the Hailo control device costs ~52 ms;
#: at a 2 s poll that would be 2.6% of a core spent watching a chip that is
#: already running two models.
_npu_temp_cache = None                                  # (when, °C or None)
NPU_TEMP_TTL_S = 10.0


def _npu_temp_cached():
    global _npu_temp_cache                              # noqa: PLW0603
    now = time.time()
    if _npu_temp_cache is not None and now - _npu_temp_cache[0] < NPU_TEMP_TTL_S:
        return _npu_temp_cache[1]
    value = _npu_temp_py().get("temp_c")
    _npu_temp_cache = (now, value)
    return value


def host_meter():
    """The few numbers the hardware footer shows, at a cost that suits a
    two-second poll: CPU, memory, CPU temperature, and the NPU's temperature
    and how many cameras are inferencing on it.

    Every field is None when it cannot be read. None is not zero -- an idle CPU
    and an unmeasurable one are different facts, and the footer renders the
    second as "-".

    `sample_ms` is how long THIS sample took to collect, so the claim that the
    monitor is cheap stays checkable from the browser rather than only from a
    comment.
    """
    started = time.perf_counter()
    out = {
        "cpu_percent": _cpu_percent_since_last(),
        "mem_percent": None,
        "cpu_temp_c": _cpu_temp_fast(),
    }
    try:
        import psutil                                   # noqa: PLC0415
        out["mem_percent"] = psutil.virtual_memory().percent
    except Exception:                                   # noqa: BLE001
        pass
    ident = _npu_identify()
    models = _active_chip_apps()
    out["npu"] = {
        "present": bool(ident.get("present")),
        "arch": ident.get("arch"),
        "temp_c": _npu_temp_cached() if ident.get("present") else None,
        # How many cameras are on the chip right now, from the apps' own
        # heartbeats -- a plain file read, no device and no subprocess.
        "models": models,
    }
    out["sample_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return out
