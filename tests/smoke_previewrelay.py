# The master's MJPEG relay ends when the viewer does.
#
# /api/detector/preview copies the app's multipart stream to the browser. When
# the browser goes (a camera switch, a closed tab) the relay has to (a) finish
# its generator and (b) close the upstream socket to the app, so the app's
# viewer thread stops encoding for nobody. Both must happen even while the
# relay is PARKED in a read on a quiet upstream -- a camera reopening sends
# nothing for seconds, and the read timeout is 30 s. Box .164 (2026-10-09)
# had eight relays open into the app's stream port while one browser held
# six previews it could not show.
#
# Runs a real loopback HTTP server (stdlib) in two moods: chatty (a part
# every 50 ms) and silent-after-one. In both, Relay.close() from another
# thread ends chunks() within a second and the server sees the client gone.
import http.client
import http.server
import os
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.services.preview_relay import Relay                 # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + str(detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


PART = (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n"
        b"JPEG\r\n")


class Upstream(http.server.BaseHTTPRequestHandler):
    """The app's /video_feed, in miniature. `mood` is set per test."""
    mood = "chatty"
    gone = threading.Event()       # the client disconnected (write failed)
    parts = 0

    def log_message(self, *a):     # quiet
        pass

    def do_GET(self):              # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            if self.mood == "short":
                self.wfile.write(PART)
                self.wfile.flush()
                return                      # upstream ends on its own
            self.wfile.write(PART)
            self.wfile.flush()
            Upstream.parts += 1
            if self.mood == "silent":
                time.sleep(3.0)             # a camera reopening: nothing to say
            while True:
                time.sleep(0.05)
                self.wfile.write(PART)
                self.wfile.flush()
                Upstream.parts += 1
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            Upstream.gone.set()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


srv = Server(("127.0.0.1", 0), Upstream)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def open_relay():
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/video_feed")
    up = conn.getresponse()
    assert up.status == 200
    return Relay(conn, up, read_timeout=30)


def drain(relay, got, done):
    """Consume chunks() on a thread, the way the response does."""
    try:
        for chunk in relay.chunks():
            got.append(chunk)
    finally:
        done.set()


for mood in ("chatty", "silent"):
    print("== upstream", mood)
    Upstream.mood = mood
    Upstream.gone.clear()
    Upstream.parts = 0
    relay = open_relay()
    got, done = [], threading.Event()
    threading.Thread(target=drain, args=(relay, got, done), daemon=True).start()
    t0 = time.time()
    while not got and time.time() - t0 < 3:
        time.sleep(0.01)
    check("the first part reaches the relay", bool(got))
    if mood == "silent":
        time.sleep(0.2)                     # make sure the read is parked
    t1 = time.time()
    relay.close()                           # the viewer went away
    check("chunks() ends within a second of close()", done.wait(1.0),
          "still running after %.1fs" % (time.time() - t1))
    check("the upstream server sees the client gone within 3 s",
          Upstream.gone.wait(4.0 if mood == "silent" else 3.0))
    check("the relay reports closed", relay.closed)
    check("close() twice is harmless", relay.close() is None)

print("== upstream ends by itself")
Upstream.mood = "short"
relay = open_relay()
chunks = list(relay.chunks())
check("the body was relayed", b"".join(chunks) == PART, chunks)
check("the relay closed itself at the end", relay.closed)

print("== the route hands the relay to the response")
with open(os.path.join(ROOT, "core", "routes", "models.py"), encoding="utf-8") as f:
    src = f.read()
prev = src.split('@router.get("/api/detector/preview"', 1)[1][:6000]
check("preview uses Relay.chunks()", "link.chunks()" in prev)
check("...and closes it as the response's background task",
      "BackgroundTask(link.close)" in prev)

srv.shutdown()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
