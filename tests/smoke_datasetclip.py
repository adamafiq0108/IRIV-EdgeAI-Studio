# A dataset clip downloads as an mp4 that actually plays.
#
# It did not. The recorder asked cv2 for `mp4v`, which writes MPEG-4 Part 2
# into an .mp4 container: the file has the right name and the right extension,
# and then Windows Media Player, QuickTime and every browser sit at 0:00 on
# it. The footage recorder had already learnt this and asks for `avc1` first;
# the dataset recorder never did.
#
# So: avc1 first, and on a board whose OpenCV was built without an H.264
# encoder the fallback clip is re-encoded with ffmpeg before anybody can
# download it. Both halves are checked here, because the fallback is the
# likely path on a Pi with the distribution's OpenCV.
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class FakeWriter:
    def __init__(self, fourcc, opens):
        self.fourcc = fourcc
        self._opens = opens
        self.released = False

    def isOpened(self):
        return self._opens

    def release(self):
        self.released = True


class FakeCv2:
    """cv2 with a named set of codecs its writer will open."""

    def __init__(self, *working):
        self.working = working
        self.made = []

    def VideoWriter_fourcc(self, *chars):
        return "".join(chars)

    def VideoWriter(self, path, fourcc, fps, size):
        w = FakeWriter(fourcc, fourcc in self.working)
        self.made.append(w)
        return w


# ---- the codec the recorder asks for -------------------------------------

cv2 = FakeCv2("avc1", "mp4v")
writer, h264 = dc._dataset_writer(cv2, "/tmp/x.mp4", 10, (640, 480))
check("H.264 is what a dataset clip is written with",
      writer.fourcc == "avc1" and h264 is True, writer.fourcc)
check("...and nothing else is opened when it works", len(cv2.made) == 1,
      str([w.fourcc for w in cv2.made]))

cv2 = FakeCv2("mp4v")                     # OpenCV built without H.264
writer, h264 = dc._dataset_writer(cv2, "/tmp/x.mp4", 10, (640, 480))
check("a board without an H.264 encoder still records",
      writer.fourcc == "mp4v" and h264 is False, writer.fourcc)
check("...and the writer it could not use is released, not leaked",
      cv2.made[0].released is True)

# ---- and the fallback clip is repaired before it is offered --------------

src = open(os.path.join(ROOT, "core", "services", "dataset_record.py"),
           encoding="utf-8").read()
body = src[src.index("def dataset_record("):src.index("def dataset_record_stop(")]
check("the recorder re-encodes a fallback clip once it is closed",
      "_to_h264(path)" in body and "elif not h264:" in body)
check("...and only that clip: a working H.264 clip is not re-encoded",
      "_to_h264" not in body.split("elif not h264:")[0])

# _to_h264 itself, against a real ffmpeg when the machine has one.
have = bool(__import__("shutil").which("ffmpeg"))
if have:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "clip.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=160x120:rate=10:duration=1",
                    "-c:v", "mpeg4", path], check=True)
    before = open(path, "rb").read()
    check("a clip that needed repairing is repaired", dc._to_h264(path) is True)
    after = open(path, "rb").read()
    check("...in place, and it is not the file it was",
          os.path.exists(path) and after != before and len(after) > 0)
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    check("...and what comes out is H.264", codec == "h264", codec)
    check("no half-written file is left beside it",
          not os.path.exists(path + ".h264.mp4"))
else:
    print("skip ffmpeg is not installed here - the repair pass is not exercised")

# No ffmpeg, nothing to repair with: the clip must survive untouched.
from core.services import dataset_record as dr               # noqa: E402
real = dr.shutil.which
dr.shutil.which = lambda _n: None
try:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "clip.mp4")
    open(path, "wb").write(b"not really a clip")
    check("without ffmpeg the recording is kept as it is, not lost",
          dc._to_h264(path) is False
          and open(path, "rb").read() == b"not really a clip")
finally:
    dr.shutil.which = real

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
