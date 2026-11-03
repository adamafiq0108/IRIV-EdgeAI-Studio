"""What the Footage page and the PDF notice show about one snapshot.

The detection app writes, beside each snapshot it takes for a Snapshot output
(systems/demo/app/snapmeta.py has the full story):

    <sid>.jpg                     the picture
    <sid>.json                    its record: detection id, rule, timer, ...
    .crops/<sid>/<n>.jpg          a close-up per detected object (optional)

This module is the master's side: it finds those files next to the picture,
shapes the record for the page, and works out the timer figures once so the
page and the PDF cannot disagree. Older snapshots have no record; their id,
moment and object are read back from the file name where the name says them,
and nothing is invented where it does not.
"""

import json
import os
import re
import time

#: A close-up index is a small number; anything else is not a path to open.
_INDEX = re.compile(r"^\d{1,3}$")


def meta_path(jpg_path):
    return os.path.splitext(jpg_path)[0] + ".json"


def crop_dir(jpg_path):
    sid = os.path.splitext(os.path.basename(jpg_path))[0]
    return os.path.join(os.path.dirname(jpg_path), ".crops", sid)


def read_meta(jpg_path):
    """The app's record of a snapshot, or None."""
    try:
        with open(meta_path(jpg_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def crop_path(jpg_path, n):
    """The file of close-up `n`, or None. `n` must be a plain number: it is
    joined onto a path."""
    if not _INDEX.match(str(n)):
        return None
    path = os.path.join(crop_dir(jpg_path), f"{int(n)}.jpg")
    return path if os.path.isfile(path) else None


def remove_extras(jpg_path):
    """Take a snapshot's record and close-ups with it."""
    try:
        os.remove(meta_path(jpg_path))
    except OSError:
        pass
    folder = crop_dir(jpg_path)
    try:
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        os.rmdir(folder)
    except OSError:
        pass


def derive(sid, mtime):
    """(unix seconds, object id) for a snapshot with no record, from its name
    (cap_<camera>_[kind_][oid<id>_]<hex ms>) and failing that its file time.
    The object id is "" when the name does not carry one."""
    parts = str(sid).split("_")
    oid = next((p[3:] for p in parts if p.startswith("oid") and len(p) > 3), "")
    ts = float(mtime or 0)
    if parts and parts[0] == "cap" and len(parts) > 1:
        try:
            ms = int(parts[-1], 16)
            if 1.0e12 < ms < 4.0e12:                  # a believable moment
                ts = ms / 1000.0
        except ValueError:
            pass
    return ts, oid


def view_timer(timer):
    """The timer as it is shown: {threshold_s, total_s, exceeded_s, ended,
    elapsed_s, kind}. `exceeded_s` is max(0, total - threshold) and only exists
    once the stay has ended: while the object is still there the total is not
    known, so neither is the excess, and none is guessed. None when the record
    has no timer, which is also how the page knows to show none."""
    if not isinstance(timer, dict):
        return None
    try:
        thr = float(timer.get("threshold_s"))
    except (TypeError, ValueError):
        return None
    if thr <= 0:
        return None
    ended = bool(timer.get("ended"))
    total = None
    if ended:
        try:
            total = round(float(timer.get("total_s")), 2)
        except (TypeError, ValueError):
            ended = False
    out = {"kind": str(timer.get("kind") or ""), "threshold_s": thr,
           "elapsed_s": timer.get("elapsed_s"), "ended": ended,
           "total_s": total,
           "exceeded_s": None if total is None else round(max(0.0, total - thr), 2)}
    try:
        if float(timer.get("tolerance_s") or 0) > 0:
            out["tolerance_s"] = float(timer["tolerance_s"])
    except (TypeError, ValueError):
        pass
    return out


def view(sid, jpg_path, camera_name="", system_name=""):
    """Everything the photo window and the PDF print, for one snapshot."""
    meta = read_meta(jpg_path)
    try:
        mtime = os.path.getmtime(jpg_path)
    except OSError:
        mtime = 0.0
    if meta:
        ts = float(meta.get("ts") or mtime)
        oid = str(meta.get("oid") or "")
    else:
        ts, oid = derive(sid, mtime)
    local = time.localtime(ts or mtime)
    compact = time.strftime("%Y%m%d_%H%M%S", local)
    cam = str((meta or {}).get("camera_name") or camera_name or "")
    crops = []
    for c in (meta or {}).get("crops") or []:
        if isinstance(c, dict) and isinstance(c.get("n"), int) and crop_path(jpg_path, c["n"]):
            crops.append({"n": c["n"], "label": str(c.get("label") or ""),
                          "track": c.get("track"),
                          "confidence": c.get("confidence"),
                          "primary": bool(c.get("primary")),
                          "box": c.get("box")})
    return {
        "id": sid,
        "has_record": bool(meta),
        "detection_id": str((meta or {}).get("detection_id")
                            or (f"{compact}_{oid}" if oid else compact)),
        "date": str((meta or {}).get("date") or time.strftime("%Y-%m-%d", local)),
        "time": str((meta or {}).get("time") or time.strftime("%H:%M:%S", local)),
        "camera": str((meta or {}).get("camera") or ""),
        "camera_name": cam,
        "device": cam or str(system_name or ""),
        "label": str((meta or {}).get("label") or ""),
        "oid": oid,
        "direction": str((meta or {}).get("direction") or ""),
        "zone": str((meta or {}).get("zone") or ""),
        "location": str((meta or {}).get("location") or ""),
        "violation": str((meta or {}).get("violation") or ""),
        "notice_title": str((meta or {}).get("notice_title") or ""),
        "function": str((meta or {}).get("function") or ""),
        "crops": crops,
        "timer": view_timer((meta or {}).get("timer")),
    }
