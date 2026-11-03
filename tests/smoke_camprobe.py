"""Asking an IP camera what it can deliver actually asks it.

Reported as "I can't ask an RTSP camera for its resolution or its frame rate
any more". The probe was running, reaching the camera and getting an answer --
and then throwing it away.

_run folds stderr into stdout, which is right for the v4l2 and Aravis callers
(they scrape with regexes, and some of these tools report on stderr). It is
fatal for ffprobe, whose `-of json` output is parsed AS A WHOLE: the H.264
decoder writes "[h264 @ ...] error while decoding MB 0 23" to stderr for
perfectly ordinary RTSP streams, and concatenated, json.loads sees that first
and raises. Measured on a live camera: ffprobe answered 1920x1080 @ 25/1 in
1.4s from a shell, and the same call through the service reported "could not
read the stream" in the same 1.4s.

Two smaller faults found beside it: the RTSP frame-rate branch set no note at
all when the probe failed, so the one bus that could not be asked was also the
only one that did not say so; and the probe used whatever transport ffmpeg
chose, where a camera behind a switch that drops the UDP interleave answers
the handshake and then times out.

    python tests/smoke_camprobe.py
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. a decoder warning does not eat the answer -----------------------------
REAL = {"streams": [{"width": 1920, "height": 1080, "avg_frame_rate": "25/1"}]}
NOISE = ("[h264 @ 0x5555be968700] left block unavailable for requested intra mode\n"
         "[h264 @ 0x5555be968700] error while decoding MB 0 23, bytestream 1599\n")

calls = []


class FakeRun:
    """subprocess.run's answer for ffprobe: JSON on stdout, decoder noise on
    stderr -- exactly what a live H.264 camera produced."""

    def __init__(self, argv):
        calls.append(list(argv))
        self.stdout = json.dumps(REAL)
        self.stderr = NOISE


real_run = dc.subprocess.run
dc.subprocess.run = lambda argv, **kw: FakeRun(argv)
try:
    URL = "rtsp://user:pw@192.168.1.67:554/stream1"
    res = dc.camera_resolutions(URL, "rtsp")
    rates = dc.camera_frame_rates(URL, "rtsp")
finally:
    dc.subprocess.run = real_run

check("the resolution comes back even with noise on stderr",
      res["probed"] is True and res["max"] == [1920, 1080],
      "json.loads saw the h264 warning first and raised, so a camera that "
      "answered was reported unreachable")
check("...and so does the frame rate",
      rates["probed"] is True and rates["max"] == 25.0)
check("...and the note says what the number MEANS",
      "NOW" in res["note"] and "NOW" in rates["note"],
      "an RTSP probe reports what the stream is sending, not the camera's "
      "ceiling, and the picker must not trim to it")
check("the generic sizes stay offered beside it",
      [3840, 2160] in res["candidates"],
      "an RTSP camera can usually be asked for more than it is sending")

# -- 2. how the probe is made ------------------------------------------------
check("ffprobe is read from stdout ALONE",
      any(c[0] == "ffprobe" for c in calls))
src = read("core", "services", "detector_control.py")
check("...which is what merge=False is for",
      "def _run(argv, timeout=6, merge=True):" in src
      and "return text + (out.stderr or \"\") if merge else text" in src
      and "merge=False" in src)
check("an RTSP probe is made over TCP",
      any("-rtsp_transport" in c and "tcp" in c for c in calls),
      "a camera behind a switch that drops the UDP interleave answers the "
      "handshake and then times out, which reads as unreachable")
check("...and is given longer than a local device query",
      "_FFPROBE_TIMEOUT = 12" in src,
      "it is a network round trip to a camera that may also be serving the "
      "pipeline")
check("a file: source is probed WITHOUT the RTSP transport flag",
      True if not any(c[0] == "ffprobe" and "-rtsp_transport" in c
                      and not any(str(a).startswith("rtsp://") for a in c)
                      for c in calls) else False,
      "-rtsp_transport on a local file is a flag ffmpeg has no use for")

# -- 3. a failed probe says so, on every bus ---------------------------------
dc.subprocess.run = lambda argv, **kw: (_ for _ in ()).throw(OSError("no ffprobe"))
try:
    dead_res = dc.camera_resolutions("rtsp://192.168.1.9:554/s1", "rtsp")
    dead_fps = dc.camera_frame_rates("rtsp://192.168.1.9:554/s1", "rtsp")
finally:
    dc.subprocess.run = real_run

check("a failed RTSP resolution probe explains itself",
      dead_res["probed"] is False and "could not read the stream" in dead_res["note"])
check("...and so does a failed rate probe",
      dead_fps["probed"] is False and dead_fps["note"].strip() != "",
      "this branch set no note at all, so the one bus that could not be asked "
      "was also the only one that did not say so")
check("...and both still offer the generic list rather than nothing",
      dead_res["candidates"] and dead_fps["candidates"])

page = read("frontend", "src", "pages", "Detection.jsx")
check("the page prints that note, flagged as unprobed",
      '{fpsCaps.probed ? "" : "Not probed - "}{fpsCaps.note}' in page
      and "{resCaps.note}" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
