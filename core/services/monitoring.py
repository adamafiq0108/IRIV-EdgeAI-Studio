# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
monitoring.py -- Live Monitoring page data (v2.0 #6).

Pure shaping (conventions sec 2). live_rows() flattens per-device real-time
status for the monitoring table; bandwidth_series() shapes the fleet
bandwidth ring buffer for the chart. Health classification is reused from
core/services/health.py.
"""

from core.services import health as health_service


def live_rows(devices, thr=None):
    """Per-device live monitoring rows (status/api/heartbeat + key metrics)."""
    rows = []
    for d in devices:
        tel = d.get("telemetry") or {}
        rows.append({
            "host_serial": d.get("host_serial"),
            "label": d.get("display_name") or d.get("label") or d.get("host_serial"),
            "online": bool(d.get("online")),
            "app_running": bool(d.get("app_running")),
            "health": health_service.classify(d, thr),
            "last_heartbeat": d.get("last_heartbeat"),
            "cpu_percent": tel.get("cpu_percent"),
            "mem_percent": tel.get("mem_percent"),
            "disk_percent": tel.get("disk_percent"),
            "cpu_temp_c": tel.get("cpu_temp_c"),
            "net_up_bps": tel.get("net_up_bps"),
            "net_down_bps": tel.get("net_down_bps"),
            # Hailo / NPU accelerator telemetry. Extra contract-legal keys the
            # device reports best-effort (contract-v1.md sec 2); any/all may be
            # absent -- a non-Hailo unit simply carries None and the page shows
            # the section empty rather than broken.
            "hailo_util_percent": tel.get("hailo_util_percent"),
            "hailo_temp_c": tel.get("hailo_temp_c"),
            "hailo_fps": tel.get("hailo_fps"),
            "hailo_tops": tel.get("hailo_tops"),
            "hailo_power_w": tel.get("hailo_power_w"),
        })
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def bandwidth_series(history):
    """Shape the bandwidth ring buffer into chart arrays (labels + up/down)."""
    labels = [h["ts"][11:19] for h in history]
    return {
        "labels": labels,
        "up": [round(h.get("up_bps", 0) / 1000.0, 1) for h in history],     # kbps
        "down": [round(h.get("down_bps", 0) / 1000.0, 1) for h in history],
    }
