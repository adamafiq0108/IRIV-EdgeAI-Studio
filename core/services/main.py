# -*- coding: utf-8 -*-
"""
main.py -- The Main page's data.

The page itself renders only the contract's standard stats keys
(docs/contract-v1.md sec 4), which is what lets one page serve every system.

aggregate_stats() is the system-wide roll-up, shared with
core/services/analytics.py, and it carries a device's extra keys too: a system
whose devices pre-aggregate rather than stream events has nowhere else to put
its numbers, and sec 4 promises extra keys are allowed. Dropping them here
would make that promise false for every widget.
"""


#: (stats key, label, sub) -- the four tiles every system gets.
TILES = (
    ("violations_today", "Violations today", "across paired devices"),
    ("violations_total", "Violations total", "all time"),
    ("events_today", "Events today", "all types"),
    ("events_total", "Events total", "all time"),
)

#: Summed as ints, always present in the roll-up even with no devices, because
#: the tiles render from them and a missing tile reads as a broken page.
STANDARD_KEYS = {k for k, _, _ in TILES} | {"uptime_s"}


#: Longest a TEXT stat may be when it is published.
#:
#: A stat rides the 4 second live poll to every open dashboard and into every
#: device roll-up, so an unbounded string (a webhook body, a stack trace, a
#: model label list) is paid for on every poll by every viewer. 120 is the
#: same ceiling a card's own words already carry (builder._clean's "text"), so
#: anything that fits on a tile fits through here.
TEXT_MAX = 120


def _is_number(value):
    # bool is an int in Python, and summing camera_running into "1 camera" is
    # not a number anyone asked for.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def text_stat(value):
    """`value` as a publishable string, or None if it is not text.

    The one place the length rule lives, so the app's reader and the device
    roll-up cannot cap at two different lengths and disagree about what a
    dashboard shows.
    """
    if not isinstance(value, str):
        return None
    return value[:TEXT_MAX]


def _as_int(value):
    # A device may publish TEXT under a standard key -- a rule that picked a
    # word, wired to a key named events_total. int("People") raises, and this
    # sum builds EVERY tile on the Main page, so one such key would blank the
    # whole page instead of one card. A numeric string still counts, exactly
    # as int() has always taken one.
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _combine(values):
    """Combine one extra stats key across devices. None means "not combinable".

    Numbers add. Dicts of numbers merge key-by-key and add -- a device that
    pre-aggregates sends buckets like {"2026-07-15": 3}, and two devices'
    counts for the same day belong in one bar, not two series. This merge is
    the whole point of a per-system master: the roll-up across its devices.

    TEXT combines only when every device says the SAME word: a rule that
    picked "People" on all four cameras has one system-wide answer, and that
    is the word. Devices that disagree have none -- "People" and "Chair" do
    not average, and picking either would put one camera's answer on a tile
    that claims to speak for the system -- so that case stays None for the
    same reason a label always did. Capped on the way out (TEXT_MAX): this
    value reaches every dashboard on a 4 second poll.

    Anything else -- a flag, a list -- is a per-device fact with no
    system-wide sum. build_devices() already surfaces those per device, so
    they are dropped here rather than guessed at.
    """
    values = [v for v in values if v is not None]
    if not values:
        return None
    if all(isinstance(v, dict) for v in values):
        out = {}
        for bucket in values:
            for key, value in bucket.items():
                if _is_number(value):
                    out[key] = out.get(key, 0) + value
        return out or None
    if all(_is_number(v) for v in values):
        return sum(values)
    if all(isinstance(v, str) for v in values):
        first = values[0]
        return text_stat(first) if all(v == first for v in values) else None
    return None


def live_local_stats(state):
    """The local detection app's stats, read FRESH from its state.json.

    The sync loop refreshes app.state.local_stats only once per cycle (~10s);
    a page polling a live number through that cache lags the video feed by up
    to a full cycle -- an operator sees 4 people on the feed and 1 on the tile.
    Reading through the ingest object costs one small-file read per request,
    which the read-path endpoints can afford. Falls back to the cached copy so
    a transient read error never blanks a page that had numbers."""
    ingest = getattr(state, "local_ingest", None)
    if ingest is not None:
        try:
            return ingest.stats()
        except Exception:                                   # noqa: BLE001
            pass
    return getattr(state, "local_stats", None)


def aggregate_stats(devices, local=None):
    """Roll every device's stats up into one system-wide dict.

    What a widget's data_fn(events, stats) receives, and what the tiles show --
    computed once, from the same numbers, so the two can never disagree.

    `local` is the LOCAL detection app's stats (standalone: no paired device);
    it joins the roll-up as one more device, so every widget key works whether
    the numbers come from a fleet or the board itself.
    """
    if local:
        devices = list(devices) + [{"stats": local}]
    out = {k: sum(_as_int(d.get("stats", {}).get(k)) for d in devices)
           for k in STANDARD_KEYS}

    extra = {}
    for device in devices:
        for key, value in (device.get("stats") or {}).items():
            if key not in STANDARD_KEYS:
                extra.setdefault(key, []).append(value)

    for key, values in extra.items():
        combined = _combine(values)
        if combined is not None:
            out[key] = combined
    # A device's camera_label is its camera source as its app wrote it; an
    # agent or a state.json from before 1.0.37 sends the password with it.
    # Every account reads this roll-up (/api/main), so it is masked here.
    if isinstance(out.get("camera_label"), str):
        from core.services.redact import mask_text          # noqa: PLC0415
        out["camera_label"] = mask_text(out["camera_label"])
    return out


def build_tiles(devices):
    """Sum each standard stat across a system's devices."""
    totals = aggregate_stats(devices)
    return [{"key": key, "label": label, "value": totals[key], "sub": sub}
            for key, label, sub in TILES]


def _ip_from_url(url):
    """Bare host/IP from a device url, for display (no scheme/port)."""
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0]


def _camera_count(device):
    cam = device.get("camera") or {}
    tel = device.get("telemetry") or {}
    if "camera_count" in tel:
        return int(tel["camera_count"] or 0)
    return 1 if cam.get("label") or cam.get("running") else 0


def build_devices(devices, thr=None):
    """Flatten what the device cards need (v2.0 rich cards).

    `app_running` False is normal, not an error: detection apps get killed by
    updates and operators while the agent stays up to say so. Health is
    classified from telemetry vs thresholds (missing telemetry = unknown).
    """
    from core.services import health as health_service
    from core.services.redact import mask_text              # noqa: PLC0415
    out = []
    for d in devices:
        telemetry = d.get("telemetry") or {}
        out.append({
            "host_serial": d.get("host_serial", ""),
            # display_name is stamped by inventory.merge_meta; fall back safely.
            "label": d.get("display_name") or d.get("label")
            or mask_text(d.get("camera", {}).get("label", "")) or d.get("host_serial", ""),
            "url": d.get("url", ""),
            "ip": _ip_from_url(d.get("url", "")),
            "online": bool(d.get("online")),
            "app_running": bool(d.get("app_running")),
            "skew_flagged": bool(d.get("skew_flagged")),
            "health": health_service.classify(d, thr),
            "folder_id": d.get("folder_id"),
            "platform_version": telemetry.get("platform_version", ""),
            "installed_apps": telemetry.get("installed_apps") or [],
            "camera_count": _camera_count(d),
            "last_heartbeat": d.get("last_heartbeat"),
            "telemetry": telemetry,
            "violations_today": int(d.get("stats", {}).get("violations_today") or 0),
        })
    return out


#: severity -> badge class. The contract's closed set (contract-v1.md sec 3),
#: which is what lets a generic page colour any system's events.
_SEVERITY_BADGE = {"crit": "badge-alert", "warn": "badge-warn", "ok": "badge-ok"}


def build_alerts(events, limit=8):
    """Newest events first, flattened for the Main page's alerts table.

    Systems that pre-aggregate (totals in stats, no event stream) legitimately
    have none -- the table renders its empty state, not an error.
    """
    newest = sorted(events, key=lambda e: e.get("ts", ""), reverse=True)[:limit]
    return [{
        "ts": (e.get("ts") or "")[11:19],
        "device": e.get("device") or e.get("host_serial", ""),
        "label": e.get("label", e.get("type", "")),
        "badge": _SEVERITY_BADGE.get(e.get("severity"), "badge-ok"),
    } for e in newest]


def build_overview(stats):
    """Compliance level for the Main page, from standard keys only.

    Share of today's events that were NOT violations: >=90% good, >=70% fair,
    below that poor. No events yet is a normal boot state, not a level.
    """
    events = stats.get("events_today") or 0
    if not events:
        return {"level": "-", "css": "", "pct": None}
    pct = round(100 * (1 - (stats.get("violations_today") or 0) / events))
    if pct >= 90:
        return {"level": "Good", "css": "c-ok", "pct": pct}
    if pct >= 70:
        return {"level": "Fair", "css": "c-warn", "pct": pct}
    return {"level": "Poor", "css": "c-danger", "pct": pct}
