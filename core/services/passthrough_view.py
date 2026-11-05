# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""passthrough_view.py -- the master's half of [video] feed = "passthrough".

The app does the work (systems/demo/app/passthrough.py): for an RTSP camera
it asks the video gateway to pull the camera's main stream itself, and the
browser watches that over WebRTC with nothing decoded or encoded in Python.
The master's part is small and lives here:

  * `view_source` survives a camera save. It is an optional key on a camera
    entry -- the main-stream address, credentials included, for a camera
    whose main stream cannot be derived from its detect URL. The camera
    writer (detector_control.set_cameras) rebuilds every entry from a
    whitelist, and the camera list the pages read is built the same way, so
    the key is carried here from the STORED entry instead: never read from a
    request, never returned in any answer, never shown to a browser.

The readiness rule (an on-demand pull is servable before its first viewer)
is in core/services/streaming.py stream_ready, beside the rule it extends.

Framework-free (docs/conventions.md sec 2).
"""


def carry_view_sources(clean, cfg):
    """COPIES of the cleaned camera entries, each with the `view_source` its
    stored namesake in `cfg` (the system's merged config) had.

    Copies, because the cleaned list is also what the camera routes answer
    with: the key goes to the file being written and nowhere else.

    Only while the camera's detect `source` is unchanged: re-pointing a camera
    at another device drops the old device's view address with it, or the
    page would show the old camera's picture under the new camera's boxes.
    (The app refuses a view_source on another host too; this is the earlier,
    quieter half of the same rule.)
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    stored = cfg.get("cameras")
    if not isinstance(stored, list):
        legacy = cfg.get("camera")
        stored = [dict(legacy, id="main")] if isinstance(legacy, dict) else []
    old = {}
    for e in stored:
        if isinstance(e, dict) and e.get("view_source"):
            old[str(e.get("id") or "")] = (str(e.get("source") or ""),
                                           str(e.get("view_source")))
    out = []
    for c in clean or []:
        c = dict(c)
        was = old.get(str(c.get("id") or ""))
        if was and was[0] == str(c.get("source") or ""):
            c["view_source"] = was[1]
        out.append(c)
    return out
