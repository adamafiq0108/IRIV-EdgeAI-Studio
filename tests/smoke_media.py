"""Footage played as a camera: naming, containment, and the source encoding.

A clip or a still opens the way a camera opens, so everything after capture --
zones, functions, tracker, outputs -- is unchanged. What is new is a
browser-supplied FILENAME turning into a path on disk, which is the part worth
pinning: a name is never allowed to name somewhere else.

    python tests/smoke_media.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import media as ms                       # noqa: E402
from core.services import detector_control as dc            # noqa: E402

DATA = tempfile.mkdtemp()
bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


# -- what may be uploaded ------------------------------------------------------
check("a video is accepted", ms.safe_name("walk.mp4"), "walk.mp4")
check("an image is accepted", ms.safe_name("shift.JPG"), "shift.jpg")
check("an executable is not", ms.safe_name("run.sh"), None)
check("nor a file with no extension", ms.safe_name("clip"), None)
check("nor an extension smuggled in the stem",
      ms.safe_name("clip.mp4.sh"), None)

# -- a filename never names somewhere else ------------------------------------
# The dangerous input is not a weird name, it is a name that is a PATH. Each of
# these must come out as an ordinary file in the media folder, or be refused.
for hostile in ("../../etc/passwd.mp4", "..\\..\\windows\\evil.mp4",
                "/etc/cron.d/x.mp4", "C:\\evil.mp4", "sub/dir/clip.mp4"):
    name = ms.safe_name(hostile)
    ok = name is None or ("/" not in name and "\\" not in name
                          and not name.startswith("."))
    check(f"{hostile!r} cannot escape", ok, True)

# ...and the resolved path is checked too, because a name that LOOKS fine is
# not proof: containment is decided on real paths.
check("a traversing name resolves to nothing",
      ms.media_path(DATA, "../../etc/passwd"), None)
check("an absolute name resolves to nothing",
      ms.media_path(DATA, "/etc/passwd"), None)
check("an empty name resolves to nothing", ms.media_path(DATA, ""), None)
inside = ms.media_path(DATA, "walk.mp4")
check("a real name stays inside the media folder",
      inside.startswith(os.path.realpath(ms.media_dir(DATA))), True)

# -- saving --------------------------------------------------------------------
entry = ms.save(DATA, "My Walk Test.mp4", b"x" * 2048)
check("a saved file is named safely", entry["name"], "My_Walk_Test.mp4")
check("...and is on disk",
      os.path.isfile(os.path.join(ms.media_dir(DATA), entry["name"])), True)
check("...recorded as a video", entry["kind"], "video")
# The source string is what the camera stores. RELATIVE, so a system exported
# as a template still finds its own footage on the next box.
check("...with a relative source", entry["source"], "file:My_Walk_Test.mp4")

entry2 = ms.save(DATA, "still.png", b"y" * 10)
check("an image is recorded as one", entry2["kind"], "image")

# A re-upload replaces rather than piling up clip_1, clip_2: re-uploading is
# what somebody does after re-filming, and the source already names the file.
ms.save(DATA, "My Walk Test.mp4", b"z" * 4096)
check("a re-upload replaces", len(ms.listing(DATA)), 2)
check("...with the new content",
      os.path.getsize(os.path.join(ms.media_dir(DATA), "My_Walk_Test.mp4")), 4096)

for why, args in (("an empty file", ("a.mp4", b"")),
                  ("an oversized file", ("a.mp4", b"x" * (ms.MAX_BYTES + 1))),
                  ("a refused type", ("a.exe", b"x"))):
    try:
        ms.save(DATA, *args)
        check(f"{why} is refused", "accepted", "rejected")
    except ValueError:
        check(f"{why} is refused", "rejected", "rejected")

# -- listing -------------------------------------------------------------------
# A file the service did not name is not offered as a source: the folder is
# not a whitelist by itself.
open(os.path.join(ms.media_dir(DATA), ".hidden.mp4"), "wb").write(b"x")
check("a file we did not name is not listed",
      [m["name"] for m in ms.listing(DATA) if "hidden" in m["name"]], [])

check("deleting works", ms.delete(DATA, "still.png"), True)
check("deleting a traversal does not", ms.delete(DATA, "../../etc/passwd"), False)
check("deleting an absent file does not", ms.delete(DATA, "nope.mp4"), False)

# -- the source opens as a camera ---------------------------------------------
# Same FFmpeg backend a network stream uses. What differs about a file is not
# how it opens but that it ENDS, which the app's grab loop rewinds past.
arg, backend = dc.open_capture_spec("file:walk.mp4")
check("a file source opens via FFmpeg", (arg, backend), ("walk.mp4", "CAP_FFMPEG"))
arg, backend = dc.open_capture_spec("file:/tmp/abs.mp4")
check("an absolute file source is passed through",
      (arg, backend), ("/tmp/abs.mp4", "CAP_FFMPEG"))
# The other sources must not have moved.
check("a USB index still opens as one", dc.open_capture_spec("0"), (0, None))
check("an RTSP URL still opens via FFmpeg",
      dc.open_capture_spec("rtsp://x/y"), ("rtsp://x/y", "CAP_FFMPEG"))

# -- the app resolves the same source ------------------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import camera as cam_mod                           # noqa: E402

cam = cam_mod.Camera({"source": "file:walk.mp4", "media_dir": DATA})
check("the app resolves a relative source against its media folder",
      cam.file_path, os.path.join(DATA, "walk.mp4"))
check("...and an absolute one as given",
      cam_mod.Camera({"source": "file:/tmp/x.mp4"}).file_path, "/tmp/x.mp4")
check("a device source has no file path",
      cam_mod.Camera({"source": "0"}).file_path, None)
check("...nor does an RTSP one",
      cam_mod.Camera({"source": "rtsp://x/y"}).file_path, None)
# A file is a real source, so the camera must not fall back to synthetic --
# that would quietly log invented detections as if they were the footage.
check("a file source is not synthetic", cam.synthetic, cam_mod.cv2 is None)

# -- a file plays at ITS rate, not the camera's ------------------------------
# The configured fps is a request made of a DEVICE ("give me 10 a second, I
# cannot use more"). Footage already is what it is: asking for 10 from a 25fps
# clip does not lighten the load, it throws away three frames in five before
# anything looks at them -- and the tracker then sees objects jump three times
# as far between frames, which is how identity breaks on the very footage
# someone uploaded to test identity.
check("the ceiling is high enough for ordinary footage",
      cam_mod.MAX_FILE_FPS >= 30.0, True)
check("a device keeps whatever rate was configured",
      cam_mod.Camera({"source": "0", "fps": 10}).fps, 10.0)
check("...and reports no native rate",
      cam_mod.Camera({"source": "0", "fps": 10}).native_fps, 0.0)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
