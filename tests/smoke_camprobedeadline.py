"""The camera-cause probe gives up on a slow answer within its time limit.

WHAT WAS WRONG. diagnose_open (app/camera.py, mirrored in
core/services/detector_control.py) asks a failed IP camera directly why it
failed, "bounded hard" at _PROBE_TIMEOUT_S (2 s). The bound was per recv():
the read loop runs until a blank line or 16 KB, so something on the camera's
port that sends one byte a second and never finishes a header -- not a
camera, a wrong port, a half-dead device -- kept every recv() inside its 2 s
and the probe running for as long as the bytes kept coming (up to ~16000 s).
The probe runs inside the app's reconnect ladder on the main loop, so every
camera of that system froze with it. Measured before the fix: a server
trickling a byte every 0.5 s held diagnose_open for the full 15 s it ran.

Pinned: against that trickling server both copies return within about one
_PROBE_TIMEOUT_S, and the two copies of _probe_ask are still the same code.

    python tests/smoke_camprobedeadline.py
"""
import ast
import os
import socket
import sys
import tempfile
import threading
import time

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


LIMIT = 1.0
for mod in (cm, dc):
    mod._PROBE_TIMEOUT_S = LIMIT

stop = threading.Event()
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(8)
PORT = srv.getsockname()[1]


def _trickle(conn):
    # Six seconds, then hang up: long enough to fail the limit loudly on the
    # old per-recv() bound, short enough that the old code fails rather than
    # hangs the suite.
    end = time.monotonic() + 6
    with conn:
        while not stop.is_set() and time.monotonic() < end:
            try:
                conn.sendall(b"x")
            except OSError:
                return
            time.sleep(0.2)          # well inside each recv()'s own timeout


def _accept():
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_trickle, args=(conn,), daemon=True).start()


threading.Thread(target=_accept, daemon=True).start()

for mod in (cm, dc):
    mod._diag_cache.clear()
    t0 = time.monotonic()
    cause = mod.diagnose_open(f"rtsp://127.0.0.1:{PORT}/live")
    took = time.monotonic() - t0
    check(f"{mod.__name__}: a trickling port is given up on in time "
          f"({took:.1f}s, limit {LIMIT}s)", took < LIMIT * 2.5, took)
    check(f"{mod.__name__}: ...and claims no cause it does not know",
          cause == "", cause)

stop.set()
srv.close()


def body(path, name):
    tree = ast.parse(open(os.path.join(ROOT, *path), encoding="utf-8").read())
    return next(ast.unparse(n) for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


for name in ("_probe_ask", "_probe_auth", "_net_cause", "diagnose_open",
             "_usb_cause", "_device_holder"):
    check(f"{name} is the same code in the app and in core",
          body(("systems", "demo", "app", "camera.py"), name)
          == body(("core", "services", "detector_control.py"), name))

print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
