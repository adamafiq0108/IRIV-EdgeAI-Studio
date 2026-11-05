# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""video_saving.py -- the [video] "video saving" switches, as the master sees
them.

Two optional switches, both OFF by default (systems/demo/config.toml):

  on_demand       the APP pauses a camera's legacy view work (paint, encode,
                  publish) while nobody watches it (app/view_gate.py). The
                  master only stores and reports it.
  view_on_detect  the PAGE opens a live tile's video only while the AI
  view_hold_s     detects something on that camera, and closes it
                  view_hold_s seconds (default 30, 2..120) after the last
                  detection (frontend/src/lib/viewOnDetect.js). Needs
                  [video] overlay = "client": the detections a page sees
                  come from the overlay messages, which server mode does
                  not send.

The same page also picks WHO DRAWS the overlay ([video] overlay, the rules in
overlay_view.py) through set_overlay: it lives here because it is the switch
view_on_detect depends on, and the page offers it right beside that one.

A page learns them from the answers that already carry the overlay mode
(overlay_view.page_info: /api/detector/webrtc, a dashboard video tile's data,
/api/hub/streams) -- never from a new poll -- and only when a switch is ON,
so with both off every one of those answers is byte for byte what it was.

The parsing mirrors app/view_gate.settings: the app cannot import core/, so
the rule lives twice and tests/smoke_videosave.py pins them together.

Framework-free (docs/conventions.md sec 2): routes/video_saving.py owns the
HTTP.
"""

DEFAULT_HOLD_S = 30
MIN_HOLD_S = 2
MAX_HOLD_S = 120

#: The keys the Detection page may write. Anything else is refused rather
#: than written: a typo would otherwise sit in the file looking like a setting.
KEYS = ("on_demand", "view_on_detect", "view_hold_s", "view_classes")

#: Why view_on_detect cannot be switched on, shown beside the switch (the
#: page adds a "Turn on browser overlay" button that calls set_overlay).
NEEDS_CLIENT = "needs boxes drawn in the browser"


def _flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in ("true", "yes", "on", "1")


#: view_classes: which detected classes open the video (the page's rule,
#: lib/viewOnDetect.js). ABSENT means every class, as before this key
#: existed; a list means only those; an empty list means none. So it is never
#: written until somebody chooses, and a system that did not choose answers
#: exactly as it always did.
MAX_CLASSES = 500
MAX_CLASS_LEN = 120


def classes_list(value):
    """A clean list of class names from a TOML/JSON value, or None when the
    value is not a list (None = every class). Order kept, duplicates and blank
    names dropped, long lists and long names cut."""
    if not isinstance(value, (list, tuple)):
        return None
    out, seen = [], set()
    for item in value:
        name = str(item if item is not None else "").strip()[:MAX_CLASS_LEN]
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= MAX_CLASSES:
            break
    return out


def hold_seconds(value):
    """view_hold_s clamped to MIN..MAX; unreadable is the default."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_HOLD_S
    return min(MAX_HOLD_S, max(MIN_HOLD_S, n))


def settings(cfg):
    """{"on_demand", "view_on_detect", "view_hold_s"} from a merged config,
    exactly as the app reads it, plus "view_classes" when somebody chose."""
    video = (cfg or {}).get("video") or {}
    if not isinstance(video, dict):
        video = {}
    out = {"on_demand": _flag(video.get("on_demand")),
           "view_on_detect": _flag(video.get("view_on_detect")),
           "view_hold_s": hold_seconds(video.get("view_hold_s"))}
    chosen = classes_list(video.get("view_classes"))
    if chosen is not None:
        out["view_classes"] = chosen
    return out


def page_info(cfg):
    """{"video_saving": {...}} when either switch is on, {} otherwise -- so
    a system that never touched them answers exactly as before."""
    s = settings(cfg)
    if not (s["on_demand"] or s["view_on_detect"]):
        return {}
    return {"video_saving": s}


def get(systems_dir, slug):
    """The switches for the Detection page, with who draws the overlay (the
    page disables view_on_detect, with NEEDS_CLIENT, in server mode)."""
    from core.services import detector_control as dc         # noqa: PLC0415
    from core.services import overlay_view                    # noqa: PLC0415
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    return {**settings(cfg), "overlay": overlay_view.settings(cfg)["mode"]}


def save(systems_dir, slug, values):
    """Write the switches named in `values` into [video] of the system's
    config.local.toml; the app picks on_demand up on its next config check
    (~2 s) and a page the rest on its next open. Returns get().

    Raises ValueError for an unknown key, or for view_on_detect switched ON
    while the overlay is drawn on the server (it would do nothing)."""
    from core.services import detector_control as dc         # noqa: PLC0415
    values = values if isinstance(values, dict) else {}
    unknown = sorted(set(values) - set(KEYS))
    if unknown:
        raise ValueError(f"unknown video settings: {', '.join(unknown)}")
    if not values:
        return get(systems_dir, slug)
    if _flag(values.get("view_on_detect")) and get(systems_dir, slug)["overlay"] != "client":
        raise ValueError(f"show video only on detection {NEEDS_CLIENT}")
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    with dc.edit_local(pkg_dir) as local:
        video = dict(local.get("video") or {})
        for key in ("on_demand", "view_on_detect"):
            if key in values:
                video[key] = _flag(values[key])
        if "view_hold_s" in values:
            video["view_hold_s"] = hold_seconds(values["view_hold_s"])
        if "view_classes" in values:
            chosen = values["view_classes"]
            if chosen is None:
                video.pop("view_classes", None)       # back to every class
            elif isinstance(chosen, (list, tuple)):
                video["view_classes"] = classes_list(chosen)
            else:
                raise ValueError("view_classes must be a list of class names, "
                                 "or null for every class")
        local["video"] = video
    return get(systems_dir, slug)


def set_overlay(systems_dir, slug, mode):
    """Write [video] overlay = mode ("client" or "server") into the system's
    config.local.toml and return (get(), previous mode).

    Written explicitly in BOTH directions, even "server" (the default): the
    file then says what the operator chose, whatever a later release makes
    the default.
    The app applies it at its next config check (~2 s, run.py's reload), no
    restart. Going to "server" also switches view_on_detect off: it would sit
    ON doing nothing, which is the state save() refuses to create.

    Raises ValueError for any other mode."""
    from core.services import detector_control as dc         # noqa: PLC0415
    from core.services import overlay_view                    # noqa: PLC0415
    mode = str(mode or "").strip().lower()
    if mode not in overlay_view.MODES:
        raise ValueError("overlay must be one of: " + ", ".join(overlay_view.MODES))
    before = get(systems_dir, slug)["overlay"]
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    with dc.edit_local(pkg_dir) as local:
        video = dict(local.get("video") or {})
        video["overlay"] = mode
        if mode != "client" and "view_on_detect" in video:
            video["view_on_detect"] = False
        local["video"] = video
    return get(systems_dir, slug), before
