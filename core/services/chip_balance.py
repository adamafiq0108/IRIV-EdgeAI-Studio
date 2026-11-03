"""The Hailo load balancer's live table, read from the apps' heartbeats.

Every detection app on the box writes one small heartbeat file into a shared
directory under the temp dir (systems/demo/app/chipshare.py), and the apps
agree on the chip's fair shares by reading each other's files. This module
only READS them, for the Settings > System card: per system and camera, the
model, the rate it is paced at, the rate it reached, its share of the chip
time in use and why it sits where it does. It never writes, never computes a
cap, and nothing it returns steers an app.

The app imports nothing from core/ and core imports nothing from the app, so
the directory name and the staleness are DUPLICATED here on purpose
(smoke_chipshare pins them equal), the same way MIN_CAMERA_FPS is.

What a heartbeat holds is ids and numbers only -- the camera id, never its
source, so no camera URL or credential can reach this table. Every field is
still re-read through a whitelist below: a file that carried anything else
would have it dropped here, not passed to the browser.
"""
import json
import os
import stat
import tempfile
import time

#: Mirrors app/chipshare.SHARE_DIRNAME and STALE_S.
SHARE_DIRNAME = "cytron_chipshare"
STALE_S = 15.0

#: The reasons an app may give, and nothing else (anything unknown reads
#: as "off" rather than as free text from a file).
REASONS = ("off", "free", "fair", "floor", "demand", "hold", "fallback")
MODES = ("off", "free", "probe", "saturated", "fallback")


def share_dir():
    return os.path.join(tempfile.gettempdir(), SHARE_DIRNAME)


def _num(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value or value < 0 or value > 1e6:
        return default
    return value


def _ts(entry):
    """A heartbeat's own clock (epoch seconds); 0 -- stale -- if it is not one."""
    try:
        value = float(entry.get("ts"))
    except (TypeError, ValueError):
        return 0.0
    return value if value == value else 0.0


def _text(value):
    return str(value or "")[:64]


def _read(path, now):
    """Every fresh heartbeat in `path`, unreadable files skipped. A symlink
    or a non-directory under that name reads as nothing at all."""
    try:
        st = os.lstat(path)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    out = []
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        full = os.path.join(path, name)
        try:
            if now - os.path.getmtime(full) > STALE_S:
                continue
            with open(full, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and now - _ts(entry) <= STALE_S:
            out.append(entry)
    return out


def snapshot(now=None, path=None):
    """The card's table. Plain values only.

    {"systems": n, "mode": the newest app's view, "capacity": chip units or
     None, "rows": [{slug, camera, model, target, fps, demand, share,
     reason}]} -- share is this camera's percent of the chip time the box
    is using right now (fps x the model's cost, over the sum)."""
    now = time.time() if now is None else now
    entries = _read(path or share_dir(), now)
    rows, newest = [], None
    for e in entries:
        if newest is None or _ts(e) > _ts(newest):
            newest = e
        cost = _num(e.get("cost"), 1.0) or 1.0
        for cam in e.get("cams") or []:
            if not isinstance(cam, dict):
                continue
            reason = cam.get("reason")
            rows.append({
                "slug": _text(e.get("slug")),
                "camera": _text(cam.get("id")),
                "model": _text(e.get("model")),
                "chip": e.get("chip") is True,
                "target": round(_num(cam.get("target")), 1),
                "fps": round(_num(cam.get("fps")), 1),
                "demand": round(_num(cam.get("demand")), 1),
                "reason": reason if reason in REASONS else "off",
                "_load": _num(cam.get("fps")) * cost if e.get("chip") is True else 0.0,
            })
    total = sum(r["_load"] for r in rows)
    for r in rows:
        r["share"] = round(100.0 * r.pop("_load") / total, 1) if total > 0 else 0.0
    rows.sort(key=lambda r: (r["slug"], r["camera"]))
    mode = (newest or {}).get("mode")
    cap = (newest or {}).get("capacity")
    return {"systems": len({r["slug"] for r in rows}),
            "mode": mode if mode in MODES else "off",
            "capacity": round(_num(cap), 1) if cap is not None else None,
            "rows": rows}
