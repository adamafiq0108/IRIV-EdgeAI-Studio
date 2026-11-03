"""A camera that will not open says WHY, in words the card can turn into a fix.

Reported in UAT: on Camera Setup a failed camera read "Fault" over a card
that printed whatever the backend said -- "cannot open camera '0'",
"camera 'rtsp://...' did not answer - check the address, the credentials and
the network path to it". cv2 answers isOpened() with one bit whatever went
wrong, so an unplugged USB camera, a rejected RTSP password and a wrong
stream path all read the same.

diagnose_open() asks again, directly, after a failure. Pinned here:
  1. an RTSP camera that wants a password and got none, one that rejected
     the password, one that took it, and a wrong stream path -- against a
     real socket speaking RTSP with a Digest challenge
  2. a refused port and an unknown host
  3. a USB node that is missing / not readable
  4. the app's copy and core's copy (the "Preview snapshot" path) give the
     SAME answer for every case above -- they are duplicated on purpose
  5. every fact phrase the backend writes has a pattern in
     frontend/src/lib/camErrors.js, which is what turns it into the sentence
  6. a failure message never carries the RTSP password: it is published to
     the camera card on every dashboard

    python tests/smoke_camcause.py
"""
import hashlib
import os
import re
import socket
import sys
import tempfile
import threading
import types
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app import camera as cm                                       # noqa: E402
from core.services import detector_control as dc                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else "\n      " + str(detail)))
    if not ok:
        bad += 1


# Windows answers a closed port by retrying the SYN for ~2 s before refusing,
# where Linux (every board) refuses at once; long enough for both.
for mod in (cm, dc):
    mod._PROBE_TIMEOUT_S = 4.0 if os.name == "nt" else 1.0

# -- a camera that speaks just enough RTSP -----------------------------------
REALM, NONCE, USER, PASSWORD = "IPCam", "abc123", "admin", "s3cr3t!"


def _expected(uri):
    ha1 = hashlib.md5(f"{USER}:{REALM}:{PASSWORD}".encode()).hexdigest()
    ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()


def _serve(conn):
    with conn:
        conn.settimeout(3)
        buf = b""
        while True:
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\r\n\r\n" in buf:
                req, buf = buf.split(b"\r\n\r\n", 1)
                lines = req.decode("latin-1").split("\r\n")
                uri = lines[0].split(" ")[1]
                cseq = next((ln.split(":", 1)[1].strip() for ln in lines
                             if ln.lower().startswith("cseq:")), "0")
                auth = next((ln.split(":", 1)[1].strip() for ln in lines
                             if ln.lower().startswith("authorization:")), "")
                if not uri.endswith("/live"):
                    code = "404 Not Found"
                elif not auth:
                    code = "401 Unauthorized"
                else:
                    resp = re.search(r'response="([0-9a-f]+)"', auth)
                    code = ("200 OK" if resp and resp.group(1) == _expected(uri)
                            else "401 Unauthorized")
                head = f"RTSP/1.0 {code}\r\nCSeq: {cseq}\r\n"
                if code.startswith("401"):
                    head += (f'WWW-Authenticate: Digest realm="{REALM}", '
                             f'nonce="{NONCE}"\r\n')
                conn.sendall((head + "\r\n").encode())


srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(16)
PORT = srv.getsockname()[1]


def _accept():
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


threading.Thread(target=_accept, daemon=True).start()

# A port nothing listens on: bound, then closed.
_tmp = socket.socket()
_tmp.bind(("127.0.0.1", 0))
CLOSED = _tmp.getsockname()[1]
_tmp.close()

CASES = [
    ("no credentials -> needs a username and password",
     f"rtsp://127.0.0.1:{PORT}/live", "needs a username and password"),
    ("wrong password -> rejected",
     f"rtsp://{USER}:wrong@127.0.0.1:{PORT}/live", "username or password rejected"),
    ("right password -> answered, the failure is the video itself",
     f"rtsp://{USER}:{PASSWORD.replace('!', '%21')}@127.0.0.1:{PORT}/live",
     "answered, but its video could not be played"),
    ("wrong stream path -> path not found",
     f"rtsp://{USER}:{PASSWORD}@127.0.0.1:{PORT}/nope", "stream path not found"),
    ("closed port -> refused",
     f"rtsp://127.0.0.1:{CLOSED}/live", f"127.0.0.1:{CLOSED} refused the connection"),
    ("unknown host -> not found",
     "rtsp://no-such-camera.invalid/live", "host no-such-camera.invalid not found"),
]

# -- 1, 2, 4. the network answers, from both copies --------------------------
for label, url, want in CASES:
    cm._diag_cache.clear()
    dc._diag_cache.clear()
    a, b = cm.diagnose_open(url), dc.diagnose_open(url)
    check(label, want in a, a)
    check("...and core's copy says the same", a == b, f"app={a!r} core={b!r}")

check("a status code rides in the fact, for the Details panel",
      "(RTSP 401)" in cm._net_cause(f"rtsp://127.0.0.1:{PORT}/live"))
check("an answer is remembered, so the reconnect ladder does not re-probe",
      cm.diagnose_open(CASES[0][1]) is cm._diag_cache[CASES[0][1]][1])

# -- 3. USB ------------------------------------------------------------------
real_isdir = os.path.isdir
node = os.path.join(tempfile.mkdtemp(), "video7")
with mock.patch("os.path.isdir",
                side_effect=lambda p: p == "/dev" or (p != "/proc" and real_isdir(p))):
    for mod in (cm, dc):
        with mock.patch.object(mod, "_usb_node", return_value=node):
            check(f"{mod.__name__}: a missing node is unplugged",
                  mod._usb_cause("7") == f"no device at {node} - not plugged in",
                  mod._usb_cause("7"))
            open(node, "w").close()
            with mock.patch("os.access", return_value=False):
                check(f"{mod.__name__}: an unreadable node is a permission problem",
                      mod._usb_cause("7") == f"permission denied on {node}",
                      mod._usb_cause("7"))
            check(f"{mod.__name__}: a present, readable, unheld node has no "
                  "cause it can name", mod._usb_cause("7") == "")
            os.remove(node)
check("a non-numeric source is not a USB question", cm._usb_cause("gige") == "")

# -- 5. every phrase has a pattern on the page --------------------------------
app_src = open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
               encoding="utf-8").read()
core_src = open(os.path.join(ROOT, "core", "services", "detector_control.py"),
                encoding="utf-8").read()
js = open(os.path.join(ROOT, "frontend", "src", "lib", "camErrors.js"),
          encoding="utf-8").read()
PHRASES = ["not plugged in", "permission denied on", "in use by another program",
           "needs a username and password", "username or password rejected",
           "access refused for this user", "stream path not found",
           "answered, but its video could not be played", "refused the connection",
           "no answer from", "answered with status", "not found"]
for phrase in PHRASES:
    check(f"{phrase!r} is written by the app and by core, and matched by the page",
          phrase in app_src and phrase in core_src and phrase in js)
check("core names the 'no GigE camera at all' case, and the page matches it",
      "no GigE camera answered" in core_src and "no GigE camera answered" in js)

# -- 6. the password stays off the card ---------------------------------------
cam = cm.Camera({"source": f"rtsp://{USER}:{PASSWORD}@127.0.0.1:{CLOSED}/live"})
cam._failed("CAM_UNREACHABLE", f"camera {cam.source!r} did not open: x")
check("the published error masks the RTSP password",
      PASSWORD not in (cam.error or "") and f"{USER}:***@" in (cam.error or ""),
      cam.error)
class _Shut:
    """cv2's answer for every failure: not opened. The real one waits out a
    30 s FFmpeg timeout first, which is the whole problem this fixes."""
    def __init__(self, *a, **k):
        pass

    def isOpened(self):
        return False

    def release(self):
        pass


try:
    import cv2                                                  # noqa: F401
    shut_cv2 = mock.patch("cv2.VideoCapture", _Shut)
except ImportError:
    # The boards get OpenCV from apt (python3-opencv, scripts/install.sh), not
    # from requirements.txt, so CI has none. What is pinned here starts AFTER
    # cv2 says "not opened", and a stand-in that only ever says that is all
    # the real one adds. (CAP_* constants read as 0.)
    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.VideoCapture = _Shut
    fake_cv2.__getattr__ = lambda name: 0
    shut_cv2 = mock.patch.dict(sys.modules, {"cv2": fake_cv2})
try:
    with shut_cv2:
        dc._open_capture(f"rtsp://{USER}:{PASSWORD}@127.0.0.1:{CLOSED}/live")
    check("the preview of a closed port fails", False, "it opened")
except RuntimeError as exc:
    check("the preview's error names the cause and masks the password",
          "refused the connection" in str(exc) and PASSWORD not in str(exc), exc)

srv.close()
print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
