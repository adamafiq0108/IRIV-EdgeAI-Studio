# While a zone is being dragged, the app takes its OWN outlines off the feed.
#
# The app burns zone shapes into the video, so an unsaved drag showed two of
# them: the browser's shape under the pointer and the app's saved copy in the
# pixels underneath. The editor now asks the app to stop drawing them, through
# a hint file with a DEADLINE -- because the asker is a browser tab, and a
# closed laptop must never leave a fleet's feeds without their zones.
#
# What is pinned here: the deadline is honoured, it expires, it is clamped
# against a hint that asks for too long, it can be cleared, it scopes to one
# camera, and NOTHING it does touches config -- if the operator discards, the
# outlines come back and the zones the app counts never changed.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import run as apprun                                  # noqa: E402
from core.services import detector_control as dc               # noqa: E402

SYSTEMS = tempfile.mkdtemp()
SLUG = "zoneedittest"
os.makedirs(os.path.join(SYSTEMS, SLUG), exist_ok=True)

ZONE = {"id": "z1", "name": "Zone 1", "kind": "area",
        "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]}


# -- the app side: a reader with the same rules the loop uses ----------------
# _hide_zone_overlay is a closure inside run(), so the check is rebuilt here
# from the module's own constants -- the point is the CONTRACT (deadline,
# clamp, camera scope), and a copy that disagreed with it would fail below.
def reader(data_dir):
    path = os.path.join(data_dir, apprun.OVERLAY_HINT_FILE)
    hint = {"checked": 0.0, "mtime": 0.0, "until": 0.0, "cameras": ()}

    def hidden(pipe_id, now):
        if now - hint["checked"] >= apprun.OVERLAY_HINT_POLL_S:
            hint["checked"] = now
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
                hint["until"] = 0.0
                hint["mtime"] = 0.0
            if mtime and mtime != hint["mtime"]:
                hint["mtime"] = mtime
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f) or {}
                    until = float(raw.get("hide_zones_until") or 0)
                    hint["cameras"] = tuple(str(c) for c in (raw.get("cameras") or ()))
                except (OSError, ValueError, TypeError):
                    until = 0.0
                    hint["cameras"] = ()
                hint["until"] = min(until, mtime + apprun.OVERLAY_HINT_MAX_S)
        if now >= hint["until"]:
            return False
        return not hint["cameras"] or str(pipe_id) in hint["cameras"]

    return hidden


dc.set_zones(SYSTEMS, SLUG, [ZONE])
DATA = dc._data_dir_cached(dc._pkg_dir(SYSTEMS, SLUG))
HINT = os.path.join(DATA, apprun.OVERLAY_HINT_FILE)

# -- nothing asked for: the app draws its zones, as it always has ------------
hidden = reader(DATA)
assert hidden("cam1", time.time()) is False
print("== no hint, no change: the outlines are drawn")

# -- editing: the outlines come off ------------------------------------------
until = dc.hide_zone_overlay(SYSTEMS, SLUG, 6)
assert until > time.time(), until
hidden = reader(DATA)
assert hidden("cam1", time.time()) is True
print("== while the editor is dragging, the app stops drawing zones")

# -- and they come back on their own -----------------------------------------
# The deadline is the whole safety story: a browser that closes mid-drag says
# nothing more, and the feed repairs itself.
assert hidden("cam1", until + 0.1) is False, "the hint outlived its deadline"
print("== the hint expires by itself -- a closed tab cannot hide zones for ever")

# -- a hint that asks for too long is cut down --------------------------------
raw = json.loads(open(HINT, encoding="utf-8").read())
raw["hide_zones_until"] = time.time() + 86400
with open(HINT, "w", encoding="utf-8") as f:
    json.dump(raw, f)
hidden = reader(DATA)
now = time.time()
assert hidden("cam1", now) is True
assert hidden("cam1", now + apprun.OVERLAY_HINT_MAX_S + 1) is False, \
    "a hint asking for a day was honoured"
# The service clamps its own writes too, so the file never carries the ask.
far = dc.hide_zone_overlay(SYSTEMS, SLUG, 86400)
assert far <= time.time() + dc.OVERLAY_HINT_MAX_S + 1, far
print("== a hint longer than the ceiling is clamped, in the writer and the reader")

# -- clearing it (a save, a discard, leaving the page) ------------------------
assert dc.hide_zone_overlay(SYSTEMS, SLUG, 0) == 0.0
hidden = reader(DATA)
assert hidden("cam1", time.time()) is False
print("== cleared on demand: a discard puts the picture back at once")

# -- scoped to the camera being edited ---------------------------------------
dc.hide_zone_overlay(SYSTEMS, SLUG, 6, ["cam1"])
hidden = reader(DATA)
now = time.time()
assert hidden("cam1", now) is True
assert hidden("cam2", now) is False, "a second camera lost its outlines"
print("== only the camera being edited loses its outlines")

# -- and none of it is a config change ---------------------------------------
# The one rule this feature must never break: it is the PICTURE. What the app
# counts, and what comes back after a restart, is untouched.
saved = dc._read_toml(dc._local_path(dc._pkg_dir(SYSTEMS, SLUG)))
assert [z["id"] for z in saved.get("zones") or []] == ["z1"], saved
assert "overlay" not in json.dumps(saved), saved
assert os.path.dirname(HINT) == DATA, HINT     # lives in data, not the package
print("== the hint never touches config -- the zones the app counts are unchanged")
print("ALL OK")
