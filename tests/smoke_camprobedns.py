"""The camera-cause probe bounds the name lookup and every address together.

WHAT WAS WRONG. _net_cause (app/camera.py, mirrored in
core/services/detector_control.py) connected with
socket.create_connection(timeout=_PROBE_TIMEOUT_S). That timeout bounds
each connect, not the name lookup in front of it -- a camera named by a
host name on a box whose DNS server is gone held getaddrinfo for 5 s a try,
on the app's reconnect ladder -- and create_connection gives EVERY address
of a multi-address name the full timeout, so a name with four dead
addresses cost 8 s, not 2.

Pinned, in both copies:
  * a lookup that never answers is given up on inside the probe limit, and
    the probe claims no cause it does not know
  * a name whose addresses all black-hole costs one limit, not one each
  * an address that answers after a dead one is still reached
  * the whole probe, requests included, ends inside twice the limit
  * the new helper is the same code in the app and in core

    python tests/smoke_camprobedns.py
"""
import ast
import os
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


LIMIT = 1.0
for mod in (cm, dc):
    mod._PROBE_TIMEOUT_S = LIMIT

# A camera that answers every request at once with 401, no challenge.
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(8)
PORT = srv.getsockname()[1]


def _serve(conn):
    with conn:
        conn.settimeout(3)
        try:
            while conn.recv(4096):
                conn.sendall(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n\r\n")
        except OSError:
            return


def _accept():
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


threading.Thread(target=_accept, daemon=True).start()

# One that takes most of the limit over every answer, and challenges again.
slow = socket.socket()
slow.bind(("127.0.0.1", 0))
slow.listen(8)
SLOW = slow.getsockname()[1]


def _serve_slow(conn):
    with conn:
        conn.settimeout(5)
        try:
            while conn.recv(4096):
                time.sleep(LIMIT * 0.95)
                conn.sendall(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n"
                             b'WWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n')
        except OSError:
            return


def _accept_slow():
    while True:
        try:
            conn, _ = slow.accept()
        except OSError:
            return
        threading.Thread(target=_serve_slow, args=(conn,), daemon=True).start()


threading.Thread(target=_accept_slow, daemon=True).start()

DEAD = ("192.0.2.1", 554)                 # TEST-NET-1: never routed
real_connect = socket.socket.connect


def blackhole_connect(self, addr):
    """A dead address: waits out the socket's timeout, then times out."""
    if addr[:2] == DEAD:
        time.sleep(self.gettimeout() or 0)
        raise TimeoutError("timed out")
    return real_connect(self, addr)


def addrs(*pairs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", p) for p in pairs]


def timed(mod, url):
    mod._diag_cache.clear()
    t0 = time.monotonic()
    out = mod.diagnose_open(url)
    return out, time.monotonic() - t0


for mod in (cm, dc):
    name = mod.__name__

    def hang(*a, **k):
        time.sleep(LIMIT * 4)
        raise socket.gaierror("too late")
    with mock.patch("socket.getaddrinfo", hang):
        cause, took = timed(mod, "rtsp://cam.example/live")
    check(f"{name}: a lookup that never answers is given up on in time "
          f"({took:.1f}s, limit {LIMIT}s)", took < LIMIT * 1.5, took)
    check(f"{name}: ...and names no cause it does not know", cause == "", cause)

    with mock.patch("socket.getaddrinfo", return_value=addrs(DEAD, DEAD, DEAD, DEAD)), \
            mock.patch.object(socket.socket, "connect", blackhole_connect):
        cause, took = timed(mod, "rtsp://cam.example/live")
    check(f"{name}: four dead addresses cost one limit, not four "
          f"({took:.1f}s)", took < LIMIT * 1.5, took)
    check(f"{name}: ...and read as no answer", "no answer from" in cause, cause)

    with mock.patch("socket.getaddrinfo",
                    return_value=addrs(("127.0.0.1", PORT))), \
            mock.patch.object(socket.socket, "connect", blackhole_connect):
        cause, took = timed(mod, f"rtsp://cam.example:{PORT}/live")
    check(f"{name}: a live address still gets its answer",
          "needs a username and password" in cause, cause)

    with mock.patch("socket.getaddrinfo",
                    return_value=addrs(DEAD, ("127.0.0.1", PORT))), \
            mock.patch.object(socket.socket, "connect", blackhole_connect):
        cause, took = timed(mod, f"rtsp://cam.example:{PORT}/live")
    check(f"{name}: an address after a dead one gets only what is left "
          f"({took:.1f}s)", took < LIMIT * 2.5 and cause in (
              "", f"no answer from cam.example:{PORT}"), (took, cause))

    # Connect eats ~0.8 limit (a dead address first), then two slow answers
    # of 0.95 limit each: 2.7 limits uncapped, so the overall deadline shows.
    def half_dead(self, addr):
        if addr[:2] == DEAD:
            time.sleep(LIMIT * 0.8)
            raise TimeoutError("timed out")
        return real_connect(self, addr)
    with mock.patch("socket.getaddrinfo",
                    return_value=addrs(DEAD, ("127.0.0.1", SLOW))), \
            mock.patch.object(socket.socket, "connect", half_dead):
        cause, took = timed(mod, f"rtsp://admin:pw@cam.example:{SLOW}/live")
    check(f"{name}: the whole probe, both requests included, ends inside "
          f"twice the limit ({took:.1f}s)", took < LIMIT * 2.4, took)

srv.close()
slow.close()


def body(path, fn):
    tree = ast.parse(open(os.path.join(ROOT, *path), encoding="utf-8").read())
    return next(ast.unparse(n) for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == fn)


for fn in ("_probe_connect", "_probe_ask", "_net_cause"):
    check(f"{fn} is the same code in the app and in core",
          body(("systems", "demo", "app", "camera.py"), fn)
          == body(("core", "services", "detector_control.py"), fn))
src = open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
           encoding="utf-8").read()
check("the probe no longer uses create_connection's unbounded lookup",
      "socket.create_connection((host, port)" not in src)

print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
