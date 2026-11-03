"""A rejected camera password is not tried again for ten minutes.

WHAT WAS WRONG. diagnose_open (app/camera.py, mirrored in
core/services/detector_control.py) remembers its answer for 30 s, then asks
the camera again -- and asking a camera whose password was wrong means
sending that password again. With the reconnect ladder retrying for ever,
that was one more failed login about every 30 s on top of FFmpeg's own, and
IP cameras lock an account out after a handful of failures.

Pinned, in both copies, against a camera that counts the logins it gets:
  * after "username or password rejected", the next 10 minutes send none
  * after them, it asks again
  * a different password (a different source URL) asks at once
  * every other answer still expires after 30 s
  * a full cache keeps its rejections and drops the rest first

    python tests/smoke_campwhold.py
"""
import os
import re
import socket
import sys
import tempfile
import threading
import time
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


for mod in (cm, dc):
    mod._PROBE_TIMEOUT_S = 4.0 if os.name == "nt" else 1.0

logins = []                     # the passwords' Authorization lines, as sent


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
                if re.search(rb"(?im)^authorization:", req):
                    logins.append(req)
                conn.sendall(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n"
                             b'WWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n')


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

WRONG = f"rtsp://admin:wrong@127.0.0.1:{PORT}/live"
OTHER = f"rtsp://admin:other@127.0.0.1:{PORT}/live"
NOAUTH = f"rtsp://127.0.0.1:{PORT}/live"
clock = [1_000_000.0]

for mod in (cm, dc):
    name = mod.__name__
    mod._diag_cache.clear()
    del logins[:]
    with mock.patch.object(mod.time, "time", lambda: clock[0]):
        first = mod.diagnose_open(WRONG)
        check(f"{name}: a wrong password is reported as rejected",
              first.startswith("username or password rejected"), first)
        check(f"{name}: ...after one login", len(logins) == 1, len(logins))
        for minutes in (1, 3, 5, 9):
            clock[0] += 60 if minutes == 1 else 120 if minutes < 9 else 240
            mod.diagnose_open(WRONG)
        check(f"{name}: nine minutes of retries send the password no more",
              len(logins) == 1, len(logins))
        check(f"{name}: ...and still say why",
              mod.diagnose_open(WRONG) == first)
        clock[0] += 61
        mod.diagnose_open(WRONG)
        check(f"{name}: after ten minutes it asks again", len(logins) == 2,
              len(logins))
        mod.diagnose_open(OTHER)
        check(f"{name}: a different password is asked at once", len(logins) == 3,
              len(logins))

        mod.diagnose_open(NOAUTH)
        before = mod._diag_cache[NOAUTH]
        clock[0] += 31
        mod.diagnose_open(NOAUTH)
        check(f"{name}: any other answer still expires after 30 s",
              mod._diag_cache[NOAUTH] is not before)

        for i in range(70):
            mod._diag_cache[f"{i}"] = (clock[0], "")
        mod.diagnose_open("99")
        check(f"{name}: a full cache keeps its rejections",
              WRONG in mod._diag_cache and OTHER in mod._diag_cache
              and len(mod._diag_cache) < 10, sorted(mod._diag_cache)[:5])

srv.close()
print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
