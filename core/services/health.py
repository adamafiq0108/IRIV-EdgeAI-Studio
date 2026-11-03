# -*- coding: utf-8 -*-
"""
health.py -- device health classification (v2.0 #7).

Pure functions (conventions sec 2). classify() maps a live device dict to
one of: offline / unknown / healthy / warning / critical, from telemetry vs
configurable thresholds (config/monitoring.toml). Missing telemetry is
"unknown", never "critical" -- an older agent that reports no metrics must
not look like a failing device.
"""

# Fallback thresholds if config/monitoring.toml is absent. Mirrors the
# committed defaults; kept here so the module works even before config loads.
_DEFAULTS = {
    "cpu_percent":  {"warning": 80, "critical": 95},
    "mem_percent":  {"warning": 85, "critical": 95},
    "disk_percent": {"warning": 85, "critical": 95},
    "cpu_temp_c":   {"warning": 70, "critical": 80},
}

_ORDER = {"offline": 0, "critical": 1, "warning": 2, "unknown": 3, "healthy": 4}


def thresholds(config_monitoring=None):
    """Merge configured thresholds over the built-in defaults."""
    cfg = (config_monitoring or {}).get("thresholds") or {}
    out = {k: dict(v) for k, v in _DEFAULTS.items()}
    for metric, levels in cfg.items():
        if metric in out and isinstance(levels, dict):
            out[metric].update(levels)
    return out


def classify(device, thr=None):
    """Return one of offline/unknown/healthy/warning/critical for a device."""
    if not device.get("online"):
        return "offline"
    telemetry = device.get("telemetry") or {}
    if not telemetry:
        return "unknown"
    thr = thr or _DEFAULTS
    level = "healthy"
    for metric, levels in thr.items():
        val = telemetry.get(metric)
        if not isinstance(val, (int, float)):
            continue
        if val >= levels.get("critical", float("inf")):
            return "critical"
        if val >= levels.get("warning", float("inf")):
            level = "warning"
    return level


def worst(states):
    """The most severe health state in an iterable (for folder/fleet roll-up)."""
    return min(states, key=lambda s: _ORDER.get(s, 99), default="unknown")
