# A "Preview snapshot" of a camera another app already owns must come from
# that app's shared /raw feed -- not from opening the device (which fails
# with "cannot open camera" while the owner holds it).
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc               # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegbody" + b"\xff\xd9"
SRC = "smoke_snapshotshare_source"
REG = os.path.join(tempfile.gettempdir(), "cytron_cameras.json")


class Feed(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Multipart MJPEG shape, like the app's /raw re-serve.
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                         + JPEG + b"\r\n")

    def log_message(self, *a):                                 # quiet
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), Feed)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

# Plant a registry entry claiming OUR pid owns the source (alive by
# definition), pointing at the toy feed. Restore the file afterwards.
before = None
try:
    with open(REG, "r", encoding="utf-8") as f:
        before = f.read()
except OSError:
    pass
reg = {}
if before:
    try:
        reg = json.loads(before) or {}
    except ValueError:
        reg = {}
reg[SRC] = {"port": port, "pid": os.getpid(), "camera": "main"}
with open(REG, "w", encoding="utf-8") as f:
    json.dump(reg, f)

try:
    out = dc._shared_feed_jpeg(SRC)
    assert out == JPEG, out
    print("== the shared-feed grab returns the owner's JPEG")

    out = dc.snapshot(SRC)
    assert out == JPEG, out
    print("== snapshot() prefers the shared feed over opening the device")

    # No registry entry: back to the device path (which raises here -- there
    # is no such camera -- but it must be the DEVICE error, not a feed one).
    reg.pop(SRC, None)
    with open(REG, "w", encoding="utf-8") as f:
        json.dump(reg, f)
    assert dc._shared_feed_jpeg(SRC) is None
    print("== without a registry entry the helper stands aside")

    # A dead port: the helper gives up quietly and lets the device try.
    reg[SRC] = {"port": port, "pid": os.getpid(), "camera": "main"}
    with open(REG, "w", encoding="utf-8") as f:
        json.dump(reg, f)
    srv.shutdown(); srv.server_close()
    assert dc._shared_feed_jpeg(SRC) is None
    print("== an unreachable owner is not an error, just a miss")
finally:
    try:
        if before is None:
            os.remove(REG)
        else:
            with open(REG, "w", encoding="utf-8") as f:
                f.write(before)
    except OSError:
        pass

print("ALL OK")
