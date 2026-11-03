"""FFmpeg captures get two decoder threads, not one per CPU.

Four RTSP cameras on a 4-core CM5 ran the box at ~98% CPU. Left alone,
OpenCV opens every FFmpeg capture with one decoder thread per CPU -- sixteen
threads for four cameras on four cores, fighting the detection loop.

[camera].ffmpeg_threads (default 2) exports OPENCV_FFMPEG_THREADS before the
first VideoCapture. NOT "threads;2" in OPENCV_FFMPEG_CAPTURE_OPTIONS: OpenCV
passes that string to the demuxer only, and setting it at all drops OpenCV's
own rtsp_flags=prefer_tcp default. [camera].ffmpeg_options sets the options
string only when asked, keeps prefer_tcp unless a transport is named, and a
variable the operator already exported is never overridden.

    python tests/smoke_ffmpegopts.py
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import camera as cam                                  # noqa: E402

T, O = cam.FFMPEG_THREADS_ENV, cam.FFMPEG_OPTIONS_ENV
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the default: two threads, the options string untouched ---------------
env = {}
cam.apply_ffmpeg_env({}, env)
check("no [camera] keys: two decoder threads", env.get(T) == "2", env)
check("...and OPENCV_FFMPEG_CAPTURE_OPTIONS NOT set (prefer_tcp stays)",
      O not in env, env)
env = {}
cam.apply_ffmpeg_env(None, env)
check("a missing [camera] table is the default too", env == {T: "2"}, env)

# -- 2. the operator's own environment wins ---------------------------------
env = {T: "4", O: "rtsp_transport;udp"}
line = cam.apply_ffmpeg_env({"ffmpeg_threads": 1,
                             "ffmpeg_options": "rtsp_transport;tcp"}, env)
check("an exported OPENCV_FFMPEG_THREADS is left alone", env[T] == "4", env)
check("an exported OPENCV_FFMPEG_CAPTURE_OPTIONS is left alone",
      env[O] == "rtsp_transport;udp", env)
check("...and the log line says so", line.count("already exported") == 2, line)

# -- 3. config knobs ---------------------------------------------------------
env = {}
cam.apply_ffmpeg_env({"ffmpeg_threads": 0}, env)
check("ffmpeg_threads = 0 leaves OpenCV's default", env == {}, env)
env = {}
cam.apply_ffmpeg_env({"ffmpeg_threads": 3}, env)
check("ffmpeg_threads = 3 exports 3", env.get(T) == "3", env)
env = {}
cam.apply_ffmpeg_env({"ffmpeg_threads": "lots"}, env)
check("a non-number falls back to the default", env.get(T) == "2", env)
env = {}
cam.apply_ffmpeg_env({"ffmpeg_options": ""}, env)
check("ffmpeg_options = \"\" leaves FFmpeg alone", O not in env, env)
env = {}
cam.apply_ffmpeg_env({"ffmpeg_options": "rtsp_transport;tcp"}, env)
check("a named transport is used as written",
      env.get(O) == "rtsp_transport;tcp", env)
env = {}
cam.apply_ffmpeg_env({"ffmpeg_options": "stimeout;5000000"}, env)
check("no transport named: prefer_tcp is kept in front",
      env.get(O) == "rtsp_flags;prefer_tcp|stimeout;5000000", env)

# -- 4. the real environment, and the app wires it before any camera opens --
saved = {k: os.environ.pop(k) for k in (T, O) if k in os.environ}
try:
    cam.apply_ffmpeg_env({})
    check("with no environ given it writes os.environ", os.environ.get(T) == "2")
finally:
    for k in (T, O):
        os.environ.pop(k, None)
    os.environ.update(saved)

src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
           encoding="utf-8").read()
main = src[src.index("\ndef main("):]
at_raw = main.find('ffmpeg_cam = dict(cfg.get("camera")')
at_ai = main.find("ai_config(cfg)")
at_apply = main.find("camera_mod.apply_ffmpeg_env(ffmpeg_cam)")
at_cam = main.find("camera_mod.Camera(")
check("run.main reads the REAL [camera] table, before ai_config stubs it",
      0 <= at_raw < at_ai, (at_raw, at_ai))
check("run.main applies it before its first Camera()",
      0 <= at_apply < at_cam, (at_apply, at_cam))

# -- 5. this OpenCV build honours it (a real FFmpeg open) --------------------
try:
    import cv2                                                 # noqa: E402
    import numpy as np                                         # noqa: E402
except ImportError:
    cv2 = None
if cv2 is None:
    print("note: no opencv here, the live thread-count check is skipped")
else:
    m = re.match(r"(\d+)\.(\d+)", cv2.__version__)
    new_enough = m and (int(m.group(1)), int(m.group(2))) >= (4, 7)
    # MPEG-4, not MJPEG: FFmpeg's MJPEG decoder is single-threaded whatever
    # it is asked, so it would report 1 and prove nothing.
    path = os.path.join(tempfile.mkdtemp(), "clip.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _i in range(5):
        w.write(np.zeros((240, 320, 3), dtype=np.uint8))
    w.release()
    # Opened in a CHILD process carrying the environment apply_ffmpeg_env
    # produced. On Linux the app's own os.environ write reaches OpenCV's
    # getenv directly (putenv); on a Windows dev box OpenCV's DLL reads a
    # different CRT's copy, so an in-process open here would test Windows,
    # not the board.
    child = {k: v for k, v in os.environ.items() if k not in (T, O)}
    cam.apply_ffmpeg_env({}, child)
    probe = ("import cv2, sys; c = cv2.VideoCapture(sys.argv[1], cv2.CAP_FFMPEG); "
             "print(int(c.isOpened()), int(c.get(cv2.CAP_PROP_N_THREADS)))")
    out = subprocess.run([sys.executable, "-c", probe, path], env=child,
                         capture_output=True, text=True, timeout=60)
    got = (out.stdout.strip().splitlines() or ["0 0"])[-1].split()
    if got[0] != "1" or not new_enough:
        print(f"note: opencv {cv2.__version__} cannot show its decoder "
              f"thread count here (needs 4.7+ and an FFmpeg backend)")
    else:
        check(f"opencv {cv2.__version__} opens an FFmpeg capture with 2 "
              f"decoder threads", got[1] == "2", out.stdout + out.stderr)

print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
