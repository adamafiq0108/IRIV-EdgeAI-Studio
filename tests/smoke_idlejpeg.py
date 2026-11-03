# Frames are encoded for VIEWERS, not for the loop's own satisfaction.
#
# Two JPEG encodes ran on every frame of every camera, forever, whether or not
# a single person was watching: one in Camera.read() to keep the /raw cache
# warm, one in _draw() for the annotated stream. On the box this was found on
# that is 44 encodes a second per camera thrown away, on the same four cores
# that run inference and drive a USB camera with no compression of its own.
#
# Both paths already had an on-demand route -- last_jpeg() encodes from the
# newest grab on the HTTP thread that asked -- so what this removes is work
# nothing was reading. Source checks, because constructing a real Camera or
# _Pipe needs a device and cv2.
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

APP = os.path.join(ROOT, "systems", "demo", "app")


def src(name):
    return io.open(os.path.join(APP, name), encoding="utf-8").read()


run_py, cam_py = src("run.py"), src("camera.py")
bad = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad.append(what)


# -- the raw cache is built where it is asked for ------------------------------
read_body = cam_py.split("    def read(self):")[1].split("\n    # ")[0]
check("read() encodes only for the synthetic camera",
      read_body.count("encode_jpeg(") == 1
      and "if self.synthetic:" in read_body.split("encode_jpeg(")[0],
      read_body)
check("...and last_jpeg still encodes on demand",
      "def last_jpeg" in cam_py and "fresh = encode_jpeg(frame)" in cam_py)

# -- the annotated encode asks whether anyone is watching ----------------------
check("the stream server publishes a viewer count",
      "server.watchers = _watchers" in run_py and "def _watchers(cam_id):" in run_py)
check("every viewer records which camera it is watching",
      re.search(r'"cam":\s*str\(cam_id or ""\)', run_py) is not None)
draw = run_py.split("def _draw(pipe, frame, detections, model_label):")[1]
# The loop's own encode is now a FALLBACK on a slow heartbeat: a viewer paints
# its own picture off the loop (below), so the only readers of this cache are
# a probe, a thumbnail, or the first instant of a new stream.
check("the loop encodes only on the idle heartbeat",
      # ...and not at all while [video] on_demand has the camera paused
      # (tests/smoke_ondemand.py): nobody is there to fall back.
      "if now_j - pipe.jpeg_at >= IDLE_JPEG_S and (not on_demand" in draw
      and "_watchers(" not in draw)
# A bare-path viewer (the fleet agent, a shared-feed consumer) resolves to "the
# first camera" and the stream server cannot tell which id that is -- so it
# must count for ALL of them. Encoding one JPEG too many costs a millisecond;
# guessing wrong costs a black stream.
watchers = run_py.split("def _watchers(cam_id):")[1].split("server.watchers")[0]
check("a bare-path viewer counts for every camera",
      'if not c["cam"] or not cid or c["cam"] == cid' in watchers, watchers)

# -- a snapshot is still the moment it fired on --------------------------------
# The stream cache is refreshed for viewers now, so an alert firing with nobody
# watching would have snapped a picture up to a second old. The annotated frame
# is kept every tick and encoded here instead.
# With [video] on_demand only its frame and overlay are kept, and painted when
# a snapshot reads them (_keep_shot / _shot_frame): the same moment's pixels.
check("the annotated frame is kept every tick",
      "stamped = _keep_shot(pipe, frame, lazy=on_demand)" in draw
      and "pipe.shot, pipe.shot_src = stamped, None" in run_py)
snap = run_py.split("    def _snap(o, event=None):")[1].split("    def _record(")[0]
check("a snapshot encodes that frame, not the stream cache",
      "shot_img = _shot_frame(p)" in snap
      and "camera_mod.encode_jpeg(shot_img)" in snap, snap)
check("...and still falls back to the caches it always used",
      "shot or p.jpeg or p.cam.last_jpeg()" in snap)

# -- the VIEWER paints its own frame -------------------------------------------
# Annotated frames used to exist only when the detection loop made one, so the
# stream ran at the inference rate and stopped dead during every inference:
# measured 200ms holes in a feed whose camera was delivering steadily at 32fps.
# Detection is slow because a chip is shared; video has no reason to be.
check("one renderer, used by the loop and by the viewer",
      run_py.count("def _paint(overlay, frame, hud=True):") == 1
      and "stamped = _paint(pipe.overlay, frame)" in draw
      and "_paint(pipe.overlay, frame)" in run_py.split("def _annotated_jpeg")[1])
# A clip carries the boxes but not the status box -- that is chrome for
# somebody watching live, and a saved clip is evidence. Painted only while the
# capture actually WANTS frames: while a clip is open, or while a run-up
# buffer is being kept for one (a Record Clip with "include before" set). The
# rest of the time the second render costs nothing, which is the point.
check("a clip is painted without the HUD, and only when capture wants frames",
      "if pipe.capture.wants_frames():" in draw
      and "_paint(pipe.overlay, frame, hud=False)" in draw)
check("the loop publishes what to draw, not only the drawing",
      "pipe.overlay = {" in draw)
check("the stream getter paints for the viewer",
      "lambda cid: _annotated_jpeg(_pipe_for(cid))" in run_py)
view = run_py.split("def _annotated_jpeg(pipe):")[1].split("\ndef ")[0]
check("it paints the NEWEST grabbed frame",
      "frame, seq = pipe.cam.latest()" in view)
# N viewers of one camera must cost ONE render: a wall of tiles is the normal
# case, and painting each one separately puts the loop's work back, times ten.
check("viewers of one camera share one render",
      "if seq == pipe.view_seq and pipe.view_jpeg is not None:" in view)
# ...and with nothing detected yet (or a synthetic camera, which publishes no
# grab) it answers with the loop's cache rather than a blank.
check("nothing to paint yet falls back to the cache",
      "return pipe.jpeg or pipe.cam.last_jpeg()" in view)
check("Camera hands out its newest frame without opening anything",
      "def latest(self):" in cam_py and "return self._latest, self._grab_seq" in cam_py)
# The feed is paced by what the CAMERA delivers now. Pacing it by cam.fps
# meant the chip balancer's share decided the VIDEO rate -- so a busy
# accelerator made the picture stutter for reasons the picture had nothing to
# do with.
check("the annotated feed is paced by the camera, not by the chip's share",
      'float(p.cam.measured_fps or 0), 1.0)' in run_py)

if bad:
    print(f"\n{len(bad)} FAILED")
    sys.exit(1)
print("ALL OK")
