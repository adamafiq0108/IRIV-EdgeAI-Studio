"""A big MJPEG camera is decoded at the size the pipeline works at.

Found on 192.168.1.234: pcb_sensata at 190% CPU, and a per-thread profile
(6 s, 200 Hz) put 60% of every sample on ONE camera's grab thread -- the 5MP
USB camera (2592x1944 MJPG) -- split between cap.retrieve(), a full 5MP JPEG
decode, and _downscale() shrinking that frame to 640. Inference was 5%.
Measured on that box, per realistic frame: 22 ms decode + 21 ms resize (a
4.05 ratio takes INTER_AREA's slow path) = 43 ms, against 8 ms for a DCT-
domain decode at 1/4 that lands at 648x486 directly.

So a V4L2 device that negotiated MJPG at more than twice max_side hands over
the compressed buffer (CAP_PROP_CONVERT_RGB = 0) and the grab loop decodes it
itself at 1/2, 1/4 or 1/8. Same 640-wide frame out, ~5x cheaper. Everything
else -- YUYV, RTSP, files, GigE, shared feeds -- is untouched, and a buffer
that will not decode flips the camera back to the old path on the spot.

    python tests/smoke_cameradct.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


from app import camera                                          # noqa: E402

# -- 1. which reduction, for which frame --------------------------------------
r = camera._dct_reduction
check("a 5MP frame decodes at 1/4 for a 640 working size", r(2592, 640) == 4,
      "2592/4 = 648 >= 640; 2592/8 = 324 is too small")
check("...a 4K-ish frame at 1/8", r(5184, 640) == 8)
check("...a 1296-wide frame at 1/2", r(1296, 640) == 2)
check("a frame under twice the limit is left alone", r(1200, 640) == 1,
      "not worth the detour through the raw buffer for a small saving")
check("...and so is one already at or under it", r(640, 640) == 1 and r(320, 640) == 1)
check("garbage in -> no reduction, not a crash",
      r(None, 640) == 1 and r("x", 640) == 1 and r(2592, 0) == 1)

# -- 2. the flag exists on this OpenCV, or the feature stays off --------------
if camera.cv2 is not None:
    check("the reduced-decode flags this relies on exist",
          all(camera._reduced_flag(k) is not None for k in (2, 4, 8)))
    check("...and an unknown reduction has none", camera._reduced_flag(3) is None)

    # A real round trip: a synthetic 5MP JPEG decoded at 1/4 is the size the
    # pipeline wants, straight from the bytes -- which is the whole point.
    import numpy as np
    img = np.zeros((1944, 2592, 3), dtype=np.uint8)
    img[::2] = 90
    ok, buf = camera.cv2.imencode(".jpg", img)
    small = camera.cv2.imdecode(buf, camera._reduced_flag(4))
    check("a 5MP JPEG decoded at 1/4 comes out 648x486",
          small is not None and small.shape[:2] == (486, 648), str(getattr(small, "shape", None)))
    check("...and _downscale takes it the last step to 640 wide",
          camera.Camera({"source": "0"})._downscale(small).shape[1] == 640)
    check("...while a buffer that is not a JPEG decodes to None, which is the fallback trigger",
          camera.cv2.imdecode(np.frombuffer(b"not a jpeg at all", dtype=np.uint8),
                              camera._reduced_flag(4)) is None)
else:
    print("skip  cv2 not installed here -- decode round trip not exercised")

# -- 3. the fourcc is read back, never assumed --------------------------------
class _Cap:
    def __init__(self, code):
        self.code = code
    def get(self, prop):
        return float(self.code)

mjpg = int.from_bytes(b"MJPG", "little")
if camera.cv2 is not None:
    check("the negotiated fourcc reads back as text", camera._fourcc_name(_Cap(mjpg)) == "MJPG")
    check("...and a device that answers nothing reads as nothing",
          camera._fourcc_name(_Cap(0)) == "")

# -- 4. the shape of the change, pinned in source -----------------------------
src = read("systems", "demo", "app", "camera.py")
check("raw mode is decided at the end of the device negotiation",
      "self._choose_raw_decode(cap, backend)" in src
      and src.index("self._stall_s = _BIG_STALL_S") < src.index("self._choose_raw_decode(cap, backend)"))
check("...V4L2 only", 'backend != getattr(cv2, "CAP_V4L2", object())' in src)
check("...MJPG only, read back", '_fourcc_name(cap) != "MJPG"' in src)
check("...never for a file or a shared feed",
      "if self.file_path or self._consuming is not None:\n            return" in src)
check("...and only when CONVERT_RGB actually took",
      "if not cap.set(cv2.CAP_PROP_CONVERT_RGB, 0):" in src)
check("the grab loop decodes the buffer at the chosen reduction",
      "frame = cv2.imdecode(raw, _reduced_flag(self._reduce))" in src)
check("...and a buffer that will not decode ends raw mode, not the camera",
      'self._leave_raw_decode(cap, "a buffer did not decode")' in src
      and "cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)" in src)
check("the full-size original is still there for whoever asks",
      "full = cv2.imdecode(self._last_raw, cv2.IMREAD_COLOR)" in src,
      "last_full() has no caller in this repo, but a store extension may")
check("...snapshotted under the same lock as the frame it belongs to",
      "self._last_raw = self._latest_raw" in src)
check("the freeze detector fingerprints the raw bytes when it has them",
      "self._note_frame(raw if raw is not None else frame)" in src,
      "a live sensor never emits two byte-identical JPEGs")
check("re-open re-decides, and starts clean",
      "self._raw_jpeg, self._reduce = False, 1\n        if cv2 is None" in src
      and "self._latest_raw = None\n        self._grab_stop = threading.Event()" in src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
