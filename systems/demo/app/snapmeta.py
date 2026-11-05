# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
snapmeta.py -- what a snapshot knows about itself.

A Snapshot output used to leave a bare JPEG: the file name said the camera, the
object id and the moment, and nothing else. The Footage page's photo window
and the PDF notice need more -- the detection id, which rule fired, the timer
that was running, a close-up of each detected object -- so each snapshot now
has a small sidecar beside it:

    snapshots/<sid>.jpg                the picture, as before
    snapshots/<sid>.json               this module's record of it
    snapshots/.crops/<sid>/<n>.jpg     one close-up per detected object, when
                                       the Snapshot node asks for them

Nothing here reads a camera or the disk apart from the two helpers at the end;
run.py (_snap) hands it the event and the detections and writes what comes
back, and tests/smoke_snapmeta.py drives it without a loop.

THE TIMER. Only real data is recorded, never an estimate:
  * a Cycle Time that fired for one object: its clock at the moment of the
    photo and the threshold it crossed (the event carries both);
  * a counting rule with a "must stay this long" setting: the object's time on
    camera so far and that setting.
Either way the TOTAL is not known when the photo is taken -- the object is
still there. It is filled in by finish_timer() when the stay ends, and until
then the record says so (`ended: false`) and the page says "still running".
"""

import time

#: Close-ups kept per snapshot, and the smallest box worth one (pixels).
MAX_CROPS = 8
MIN_CROP_PX = 12
#: Room around the box, as a share of its size, so the object is not clipped.
CROP_PAD = 0.12


def stamp_parts(ts):
    """(date "YYYY-MM-DD", time "HH:MM:SS", compact "YYYYMMDD_HHMMSS") in the
    box's own clock."""
    t = time.localtime(ts)
    return (time.strftime("%Y-%m-%d", t), time.strftime("%H:%M:%S", t),
            time.strftime("%Y%m%d_%H%M%S", t))


def detection_id(ts, oid=""):
    """"20261021_091801_29400": the moment, then the object when it is known."""
    compact = stamp_parts(ts)[2]
    oid = "".join(c for c in str(oid or "") if c.isalnum())[:12]
    return f"{compact}_{oid}" if oid else compact


def violating(dets, accepts=None, has_filter=False, zone="", oid=""):
    """The detections that are the VIOLATION, out of everything in the frame.

    A frame holds every class the model sees (holding, not holding, a person
    ...); a close-up of each would bury the one that matters. What counts:

      * the firing rule has a class filter: the detections of those classes
        (`accepts(det)` says so), and, when the rule is about a zone, only the
        ones in that zone;
      * no class filter: the detections the zone engine flagged `violation`;
      * neither says anything: just the object the event is about (`oid`), or
        nothing -- never everything, which is what this exists to avoid.
    """
    out = []
    for d in dets or ():
        if not isinstance(d, dict):
            continue
        if has_filter:
            if accepts is None or not accepts(d):
                continue
            if zone and not (d.get("zone") == zone or zone in (d.get("zones") or ())):
                continue
            out.append(d)
        elif d.get("violation"):
            out.append(d)
    if not out and oid != "" and oid is not None:
        out = [d for d in (dets or ()) if isinstance(d, dict)
               and d.get("track_id") is not None and str(d.get("track_id")) == str(oid)]
    return out


def crop_boxes(dets, frame_w, frame_h, oid="", limit=MAX_CROPS):
    """The close-ups to cut: [{n, label, track, confidence, box, primary}], the
    object that caused the firing (`oid`) first, then the most confident.
    `box` is the padded pixel rectangle inside the frame; a box too small to
    show anything is skipped."""
    out = []
    for d in dets or ():
        box = d.get("box") if isinstance(d, dict) else None
        if not box or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        w, h = x2 - x1, y2 - y1
        if w < MIN_CROP_PX or h < MIN_CROP_PX:
            continue
        px, py = w * CROP_PAD, h * CROP_PAD
        rect = [max(0, int(x1 - px)), max(0, int(y1 - py)),
                min(int(frame_w), int(x2 + px)), min(int(frame_h), int(y2 + py))]
        if rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
            continue
        track = d.get("track_id")
        out.append({"label": str(d.get("label") or ""),
                    "track": None if track is None else track,
                    "confidence": _num(d.get("confidence", d.get("score"))),
                    "box": rect,
                    "primary": bool(oid) and str(track) == str(oid)})
    out.sort(key=lambda c: (not c["primary"], -(c["confidence"] or 0.0)))
    out = out[:max(0, int(limit))]
    for n, c in enumerate(out):
        c["n"] = n
    return out


def _num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def timer_from_event(event, presence=None):
    """The timer behind a firing, or None when no real timer data exists.

    `event.detail` of a Cycle Time's per-object alarm carries `duration_s` (the
    object's clock when it crossed) and `threshold_s`. `presence`, when given,
    is what a counting rule knows of the object: {threshold_s, elapsed_s} (see
    FunctionEngine.presence). A rule with no timer, or a threshold of 0, has no
    timer to show."""
    detail = (event or {}).get("detail") or {}
    fn = str(detail.get("function") or "")
    thr, elapsed, kind = None, None, ""
    if detail.get("threshold_s") is not None and detail.get("duration_s") is not None:
        thr, elapsed, kind = _num(detail["threshold_s"]), _num(detail["duration_s"]), "cycle"
    elif presence and presence.get("threshold_s"):
        thr, elapsed, kind = _num(presence["threshold_s"]), _num(presence.get("elapsed_s")), "count"
    if thr is None or elapsed is None or thr <= 0 or elapsed < 0:
        return None
    out = {"kind": kind, "function": fn, "track": detail.get("track"),
           "threshold_s": thr, "elapsed_s": elapsed, "total_s": None,
           "ended": False}
    tol = _num((presence or {}).get("tolerance_s")) if kind == "count" else None
    if tol and tol > 0:
        out["tolerance_s"] = tol
    return out


def finish_timer(meta, total_s):
    """Record the real total once the stay is over. Returns True if it changed
    the record. Never moves a total backwards, and ignores nonsense."""
    timer = (meta or {}).get("timer")
    total = _num(total_s)
    if not timer or total is None or total < 0:
        return False
    timer["total_s"] = max(total, timer.get("elapsed_s") or 0.0)
    timer["ended"] = True
    return True


def view_timer(timer):
    """What the page and the report show of a timer, calculated here once so
    they cannot disagree: {threshold_s, total_s, exceeded_s, ended, elapsed_s}
    with exceeded_s = max(0, total - threshold - tolerance) (the tolerance is
    time the object was allowed, so it is not violation), or None when the stay is still
    running (the total is not known, so neither is the excess)."""
    if not isinstance(timer, dict) or not timer.get("threshold_s"):
        return None
    thr = float(timer["threshold_s"])
    total = timer.get("total_s") if timer.get("ended") else None
    out = {"kind": timer.get("kind") or "", "threshold_s": thr,
           "elapsed_s": timer.get("elapsed_s"), "ended": bool(timer.get("ended")),
           "total_s": None if total is None else round(float(total), 2),
           "exceeded_s": None}
    if (_num(timer.get("tolerance_s")) or 0) > 0:
        out["tolerance_s"] = _num(timer["tolerance_s"])
    if total is not None:
        out["exceeded_s"] = round(max(0.0, float(total) - thr
                                      - (_num(timer.get("tolerance_s")) or 0.0)), 2)
    return out


def build_meta(sid, ts, camera_id, camera_name, label, oid, event, location,
               crops, timer, violation="", notice_title="", page_name=""):
    """The sidecar record. Every field is something that was known at the time
    of the photo; the optional ones are absent, not blank."""
    detail = (event or {}).get("detail") or {}
    date, clock, _ = stamp_parts(ts)
    meta = {"v": 1, "id": sid, "ts": round(float(ts), 3),
            "date": date, "time": clock,
            "detection_id": detection_id(ts, oid),
            "camera": str(camera_id or ""), "camera_name": str(camera_name or ""),
            "label": str(label or ""), "oid": str(oid or "")}
    if detail.get("function"):
        meta["function"] = str(detail["function"])
    if (event or {}).get("type"):
        meta["event_type"] = str(event["type"])
    if detail.get("direction"):
        meta["direction"] = str(detail["direction"])
    if detail.get("zone"):
        meta["zone"] = str(detail["zone"])
    if location:
        meta["location"] = str(location)[:120]
    if violation:
        meta["violation"] = str(violation)[:120]
    if notice_title:
        meta["notice_title"] = str(notice_title)[:60]
    if page_name:
        meta["page_name"] = str(page_name)[:60]
    if crops:
        meta["crops"] = [dict(c) for c in crops]
    if timer:
        meta["timer"] = timer
    return meta
