# A system nobody has pointed at hardware yet must NOT run.
#
# It used to scaffold with source = "synthetic" and start happily against a
# generated picture: counts ticked, zones armed, the hub card read healthy --
# a system that looked like it was watching something while watching nothing.
# And when a real camera died, the app swapped in that same synthetic feed,
# so a disconnected camera also looked alive. Both paths are gone: an unset
# camera is unset, and a dead camera stays dead (and says so) while the
# reconnect ladder keeps trying the real device.
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import detector_control as d                # noqa: E402
from core.services import provision                            # noqa: E402

# 1. a scaffolded system carries NO camera source
assert 'source = ""' in provision._APP_CONFIG_TEMPLATE, \
    "a fresh system must scaffold with no camera, not a synthetic one"
assert 'source = "synthetic"' not in provision._APP_CONFIG_TEMPLATE

# 2. start() refuses a system whose cameras have no source
src = inspect.getsource(d.start)
assert "no camera is set up" in src, \
    "start() must refuse a system with no camera chosen"
assert "Camera Setup" in src, "the refusal must say where to fix it"
# ...and it must check ENABLED cameras' sources, not merely that a camera
# row exists (a disabled or blank row is not a camera). That check lives in
# live_cameras() since ff483696 -- start() asks it -- so the behaviour is
# pinned on the helper, not on the words that used to be inline in start().
assert "live_cameras(" in src, "start() must ask live_cameras() which cameras count"
live_src = inspect.getsource(d.live_cameras)
assert "enabled" in live_src and "source" in live_src
assert '"synthetic"' in live_src, "a synthetic source must count as unset"

# 3. the app never invents a synthetic camera
with open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
          encoding="utf-8") as f:
    cam_src = f.read()
assert 'self.config.get("source", "")' in cam_src, \
    "Camera() must not default an unset source to synthetic"
assert 'self.config.get("source", "synthetic")' not in cam_src

with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
assert '"source": "synthetic"' not in run_src, \
    "the reconnect ladder must not fall back to a synthetic feed"
assert "camera disconnected:" in run_src, \
    "a camera that cannot be reopened must report itself disconnected"

# 4. an unset source resolves to no capture spec at all (so open() reports
#    it as a camera error rather than handing "" to a backend)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import camera as camera_mod                           # noqa: E402

cam = camera_mod.Camera({"source": "", "width": 320, "height": 240})
assert cam._capture_spec() is None, "an empty source must resolve to nothing"
assert cam.open() is False, "an unset camera must not open"
assert "no camera chosen" in (cam.error or ""), cam.error
print("unset camera ->", cam.error)

# 5. the master's status payload does not invent one either
dc_src = inspect.getsource(d)
assert 'get("source", "synthetic")' not in dc_src, \
    "the status/camera-list payloads must not default to synthetic"

# 6. the placeholder row must not wedge the system: every whole-list rewrite
#    (add a second camera, toggle, remove) re-validates EVERY stored row, and
#    refusing the scaffold's unset row made "Add camera" fail with "camera
#    source is required" on any system whose first camera was never set up.
import tempfile                                                # noqa: E402
SD = tempfile.mkdtemp()
pkg = os.path.join(SD, "t1")
os.makedirs(pkg)
with open(os.path.join(pkg, "config.local.toml"), "w", encoding="utf-8") as f:
    f.write('[[cameras]]\nid = "main"\nsource = ""\nwidth = 640\n'
            'height = 480\nfps = 10\nenabled = true\n')
cams = d.add_camera(SD, "t1", {"source": "0", "name": "USB cam"})
by_id = {c["id"]: c for c in cams}
assert "main" in by_id, "the placeholder row must survive the rewrite"
assert by_id["main"]["enabled"] is False and by_id["main"]["kind"] == "unset", \
    "an unset row is carried DISABLED, never counted as running"
new = next(c for c in cams if c["id"] != "main")
assert new["source"] == "0" and new["enabled"] is True
# ...but a NEW entry still needs a real source,
try:
    d.add_camera(SD, "t1", {"source": ""})
    raise AssertionError("an empty NEW camera was accepted")
except ValueError as e:
    assert "camera source is required" in str(e)
# ...and the placeholder cannot be switched on -- there is nothing to run.
try:
    d.enable_camera(SD, "t1", "main", True)
    raise AssertionError("enabled a camera with no source")
except ValueError as e:
    assert "no source yet" in str(e)

# 7. EVERY camera is removable -- the last one included. A system with none
#    is a blank canvas: it cannot start, and the page offers to add one. The
#    scaffold's base config keeps its legacy [camera] table forever, so the
#    explicit empty list must not fall back to it and resurrect the row.
with open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[camera]\nsource = ""\nwidth = 640\nheight = 480\nfps = 10\n')
for cam in list(d.cameras(SD, "t1")):
    d.remove_camera(SD, "t1", cam["id"])
assert d.cameras(SD, "t1") == [], \
    "an emptied camera list must stay empty, not resurrect the legacy table"
# ...and adding after the wipe works: the blank canvas is a fresh start.
cams = d.add_camera(SD, "t1", {"source": "1", "name": "again"})
assert len(cams) == 1 and cams[0]["source"] == "1"

# 7b. the PAGE agrees an explicit empty list is empty: its legacy fallback
#     (one rail row from `status.camera`) must only fire when the payload has
#     no `cameras` at all, and must carry an id -- the ghost row it otherwise
#     invented could not be removed ("no camera None").
with open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
          encoding="utf-8") as f:
    page_src = f.read()
assert "if (Array.isArray(status.cameras)) return status.cameras" in page_src, \
    "camListOf must honour an explicitly empty cameras list"
assert 'status.camera ? [{ id: "main", ...status.camera }]' in page_src, \
    "the legacy fallback row needs the id the legacy shape has always meant"

# 8. the APP's duplicated reader agrees: an explicit empty list is empty
#    there too, or a wiped system would capture the legacy row regardless.
from app import run as app_run                                 # noqa: E402
assert app_run._camera_list({"cameras": [],
                             "camera": {"source": "0"}}) == [], \
    "the app must not resurrect the legacy [camera] row on an emptied list"
assert app_run._camera_list({"camera": {"source": "0"}}), \
    "a config with ONLY the legacy table must still run it"

# 9. A `file:` camera survives a settings edit. The pipe's runtime-only keys
#    (media_dir) are stamped by the constructor, not carried in the config
#    entry -- so the hot-reload's spec swap dropped them, and every file
#    camera then resolved to a bare filename: "cannot open camera
#    'file:clip.mov'" for a clip sitting in the system's own media folder.
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    hot = f.read()
swap = hot.split('spec["_stream_port"] = stream_port', 1)[1][:1200]
assert 'spec["media_dir"]' in swap,     "a camera spec swap must keep media_dir, or file: sources break"

# 10. The disconnect message reports the CAUSE, never its own previous text:
#     it is written back to cam.error, so re-wrapping nested it three deep.
cam = camera_mod.Camera({"source": "file:missing.mov", "width": 320,
                         "height": 240})
cam.error = ("camera disconnected: 'file:x.mov' gave no frames after 3 "
             "reconnect attempts (cannot open camera 'file:x.mov')")
assert "camera disconnected: " in hot
assert "if cause.startswith(\"camera disconnected: \")" in hot,     "the retry must unwrap its own prefix instead of nesting it"

print("smoke_nocamera OK")
