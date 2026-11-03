# The live feed's status box: four rows an operator can switch off.
#
# The box (model / camera / hailo fps / camera fps) is stamped INTO the JPEG by
# the detection app, not drawn over it by the page. That is what makes it
# appear on every viewer -- the Camera Setup feed, the hub's Live Preview, a
# recorded clip, a browser pointed straight at the stream URL -- and it is
# exactly why hiding a row cannot be a page-side toggle: there is no overlay
# for a page to hide. It has to be config the app reads.
#
# The two rules that matter:
#   * an absent switch means SHOWN, so a system configured before the switches
#     existed looks exactly as it did;
#   * all four off draws no box at all, which is the whole point for a wall
#     display.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc              # noqa: E402

SYSTEMS = os.path.join(ROOT, "systems")
SLUG = "demo"

# -- defaults -----------------------------------------------------------------
rows = dict(dc.HUD_ROWS)
assert list(rows) == ["model", "camera", "hailo_fps", "camera_fps"], rows
print("== the box has four rows:", list(rows))

CFG = tempfile.mkdtemp()
pkg = os.path.join(CFG, SLUG)
os.makedirs(os.path.join(pkg, "app"), exist_ok=True)
io.open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8").write("[app]\n")

hud = dc.get_hud(CFG, SLUG)
assert hud == {k: True for k in rows}, hud
print("== a config with no [hud] table shows every row")

# -- switching ----------------------------------------------------------------
after = dc.set_hud(CFG, SLUG, {"camera_fps": False, "hailo_fps": False})
assert after["camera_fps"] is False and after["hailo_fps"] is False, after
assert after["model"] is True and after["camera"] is True, after
print("== switching two off leaves the other two alone:", after)

# It is PERSISTED, not just returned -- the app reads the file, not this call.
assert dc.get_hud(CFG, SLUG) == after
local = io.open(os.path.join(pkg, "config.local.toml"), encoding="utf-8").read()
assert "[hud]" in local, local
print("== and it is written to config.local.toml, which is what the app polls")

# Back on again.
assert dc.set_hud(CFG, SLUG, {"camera_fps": True})["camera_fps"] is True
print("== and switches back on")

# A typo is refused rather than written: a key that means nothing sitting in
# the file looks like a setting somebody chose.
try:
    dc.set_hud(CFG, SLUG, {"camrea_fps": False})
    raise AssertionError("an unknown row was written")
except ValueError as exc:
    assert "camrea_fps" in str(exc), exc
    print("== an unknown row is refused:", exc)

# -- the app's half -----------------------------------------------------------
APP = io.open(os.path.join(SYSTEMS, "demo", "app", "run.py"),
              encoding="utf-8").read()
draw = APP[APP.index("def _draw("):]
draw = draw[:draw.index("\n    try:")]
# Every key this service writes has to be the key the app tests, or a switch
# moves nothing.
for key in rows:
    assert f'"{key}"' in draw, f"the app never checks {key}"
# Absent = shown. `.get(key, True)` is the whole rule.
assert 'hud_cfg.get(row[2], True)' in draw, draw
print("== the app reads all four keys, and treats an absent one as shown")

# Read LIVE, like [capture] -- a switch that needed a restart to take effect is
# a switch an operator presses twice and then reports as broken.
poll = APP[APP.index("if now >= next_cfg_check:"):]
poll = poll[:poll.index("if gst_mode:")]
assert "hud_cfg.clear()" in poll and 'new_cfg.get("hud")' in poll, poll
print("== and re-reads them on the config poll, without a restart")

# All four off must draw NOTHING, which rests on draw_hud's empty-list guard.
CAM = io.open(os.path.join(SYSTEMS, "demo", "app", "camera.py"),
              encoding="utf-8").read()
hud_fn = CAM[CAM.index("def draw_hud("):]
hud_fn = hud_fn[:hud_fn.index("\ndef ", 5)]
assert "if frame is None or not lines:" in hud_fn, hud_fn
print("== with every row off, no box is drawn at all")

# -- the route ----------------------------------------------------------------
RT = io.open(os.path.join(ROOT, "core", "routes", "models.py"),
             encoding="utf-8").read()
post = RT[RT.index('name="detector.set_hud"'):]
post = post[:post.index("@router.")]
# The gate is a CAPABILITY master ticks per account (core/services/caps.py),
# not the master tier itself -- an operation lead deciding what the picture
# shows is not the box owner's decision to make for them.
assert 'perm.require(request, "detection.hud")' in post, \
    "anybody can change the feed overlay"
assert "_system_context" in post, "the slug comes from the caller"
print("== the route asks for the HUD capability and takes its system from the server")
print("ALL OK")
