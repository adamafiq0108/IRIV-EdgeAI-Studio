# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
overview.py -- Platform Overview + Camera Overview shaping (v2.0 #8, #17).

Pure functions (conventions sec 2). platform_stats() rolls the fleet up into
the headline numbers; cameras() lists every connected camera across devices.
Fleet bandwidth here is a DELIBERATE sum of per-device rates (meaningful),
not a stats auto-sum.
"""

from core.services import health as health_service


def platform_stats(devices, events, thr=None):
    """Fleet-wide headline stats for the Platform Overview page."""
    online = [d for d in devices if d.get("online")]
    running_apps = sum(1 for d in devices if d.get("app_running"))
    cameras_total = sum(_camera_count(d) for d in devices)
    up = sum(int((d.get("telemetry") or {}).get("net_up_bps") or 0) for d in devices)
    down = sum(int((d.get("telemetry") or {}).get("net_down_bps") or 0) for d in devices)

    health_counts = {"healthy": 0, "warning": 0, "critical": 0,
                     "offline": 0, "unknown": 0}
    for d in devices:
        health_counts[health_service.classify(d, thr)] += 1

    alerts = sum(1 for e in events if e.get("severity") in ("warn", "crit"))
    return {
        "devices_total": len(devices),
        "devices_online": len(online),
        "devices_offline": len(devices) - len(online),
        "cameras_total": cameras_total,
        "running_apps": running_apps,
        "bandwidth_up_kbps": round(up / 1000.0, 1),
        "bandwidth_down_kbps": round(down / 1000.0, 1),
        "health": health_counts,
        "active_alerts": alerts,
        "warnings": health_counts["warning"] + health_counts["critical"],
    }


#: The board-level verdict as a NUMBER, so an ordinary widget can bind to it.
#: Widgets read `stats`; the verdict is derived from zone actions, which are
#: config, not stats. Publishing it as a stat key is what lets the Dashboard's
#: violation indicator be a normal draggable widget instead of hardcoded UI.
COMPLIANCE = {"ok": 1, "violating": 0, "empty": -1, "unknown": -1}


def zone_overview(status, stats):
    """Turn a system's zones + live stats into what the Dashboard shows.

    Returns {zones, verdict, compliance, has_violation_action}. Each zone
    carries the value CARDS its actions imply -- a Count action means "in zone
    now" and "total seen", a Violation action means a violation counter and a
    red/green verdict, a Cycle action means live and average stay.

    Pure: it reads a status dict and a stats dict, so both the overview API and
    the dashboard renderer can call it and cannot drift apart.
    """
    from core.services import functions as functions_service

    zones, any_violation, any_object = [], False, False
    for z in status.get("zones") or []:
        zid = z.get("id")
        cards, verdict = [], None
        for a in z.get("actions") or []:
            # Labels and stat keys come from the compiler's own mapping, so a
            # card can never be bound to a key nothing publishes.
            cards.extend(functions_service.zone_action_cards(z, a))
            if a.get("type") != "violation":
                continue
            limit = int(a.get("limit") or 0)
            # How many are inside RIGHT NOW. Read from the zone engine's own
            # stat rather than a function: every area zone publishes it whether
            # or not the operator also added a Count action, so the verdict
            # cannot depend on which actions happen to exist.
            now = stats.get(f"zone_{zid}_count")
            if now is None:
                now = stats.get(f"line_{zid}_occupancy")
            if isinstance(now, bool) or not isinstance(now, (int, float)):
                # The verdict is a COMPARISON, and a stat is no longer always
                # a number: a rule that picks a word publishes text now, and a
                # paired device may publish one under any key it likes.
                # "People" > limit raises, and this runs inside the single
                # payload the Main page and the Dashboard both render from --
                # so an unusable value costs one zone's badge, never the whole
                # page.
                now = None
            # The verdict the page badges: no object / ok / violating.
            if now is None:
                verdict = "unknown"
            elif now <= 0:
                verdict = "empty"
            elif now > limit:
                verdict = "violating"
            else:
                verdict = "ok"
            if now:
                any_object = True
            if verdict == "violating":
                any_violation = True
        zones.append({"id": zid, "name": z.get("name"), "kind": z.get("kind"),
                      "color": z.get("color"), "enabled": z.get("enabled", True),
                      "actions": z.get("actions") or [], "cards": cards,
                      "verdict": verdict})
    in_frame = stats.get("objects_in_frame")
    verdict = ("violating" if any_violation
               else "ok" if (any_object or in_frame) else "empty")
    return {
        "zones": zones,
        "verdict": verdict,
        "compliance": COMPLIANCE[verdict],
        "has_violation_action": any(
            a.get("type") == "violation"
            for z in (status.get("zones") or []) for a in (z.get("actions") or [])),
    }


def _camera_count(device):
    tel = device.get("telemetry") or {}
    if "camera_count" in tel:
        return int(tel["camera_count"] or 0)
    cam = device.get("camera") or {}
    return 1 if cam.get("label") or cam.get("running") else 0


def cameras(devices):
    """Every camera across the fleet (v2.0 #8). Extends as adapters report
    richer camera info; today: name/status per device + owning device."""
    out = []
    for d in devices:
        cam = d.get("camera") or {}
        tel = d.get("telemetry") or {}
        if not (cam.get("label") or cam.get("running") or tel.get("camera_count")):
            continue
        out.append({
            "device": d.get("display_name") or d.get("label") or d.get("host_serial"),
            "host_serial": d.get("host_serial"),
            "name": cam.get("label") or "camera",
            "ip": tel.get("camera_ip") or "",
            "status": "running" if cam.get("running") else (
                "online" if d.get("online") else "offline"),
            "resolution": tel.get("camera_resolution") or "",
            "fps": tel.get("camera_fps") or "",
        })
    out.sort(key=lambda c: c["device"].lower())
    return out
